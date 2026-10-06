"""HTTP endpoints for uploaded geospatial files."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import FileNotReadyError, FileRecordNotFoundError
from app.db.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.db.session import get_db
from app.schemas import (
    ErrorResponse,
    FeatureOut,
    FileDetail,
    FileSummary,
    MeasurementOut,
    MeasurementsResponse,
    MeasurementSummary,
    Page,
)
from app.services.processor import process_file, store_upload

router = APIRouter(prefix="/api/files", tags=["files"])

DB = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=1000, description="Page size")]
Offset = Annotated[int, Query(ge=0, description="Number of items to skip")]

_ERRORS = {
    404: {"model": ErrorResponse, "description": "File not found"},
}


def _get_file(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise FileRecordNotFoundError(f"File '{file_id}' not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status is not FileStatus.COMPLETED:
        detail = f"File is {record.status.value}"
        if record.error:
            detail += f": {record.error}"
        raise FileNotReadyError(detail)


def _file_detail(db: Session, record: UploadedFile) -> FileDetail:
    rows = db.execute(
        select(Feature.geometry_type, func.count())
        .where(Feature.file_id == record.id)
        .group_by(Feature.geometry_type)
    ).all()
    detail = FileDetail.model_validate(record)
    detail.geometry_types = {(gtype or "None"): count for gtype, count in rows}
    return detail


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileDetail,
    summary="Upload and process a geospatial file",
    responses={
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": FileDetail, "description": "File stored but could not be processed (status FAILED)"},
    },
)
def upload_file(
    db: DB,
    file: Annotated[UploadFile, File(description="A .kml file, or a .zip containing a Shapefile (.kmz also accepted)")],
):
    record = store_upload(db, file.file, file.filename or "upload")
    record = process_file(db, record)
    detail = _file_detail(db, record)
    if record.status is FileStatus.FAILED:
        return JSONResponse(status_code=422, content=detail.model_dump(mode="json"))
    return detail


@router.get("/", response_model=Page[FileSummary], summary="List uploaded files")
def list_files(db: DB, limit: Limit = 50, offset: Offset = 0):
    total = db.scalar(select(func.count()).select_from(UploadedFile)) or 0
    rows = db.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page[FileSummary](
        count=total, limit=limit, offset=offset, results=[FileSummary.model_validate(r) for r in rows]
    )


@router.get("/{file_id}/", response_model=FileDetail, responses=_ERRORS, summary="Get file information")
def get_file(file_id: str, db: DB):
    return _file_detail(db, _get_file(db, file_id))


@router.get(
    "/{file_id}/features/",
    response_model=Page[FeatureOut],
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
    summary="List features with geometry, CRS and properties",
)
def list_features(
    file_id: str,
    db: DB,
    limit: Limit = 100,
    offset: Offset = 0,
    geometry_type: Annotated[str | None, Query(description="Filter, e.g. Polygon")] = None,
):
    record = _get_file(db, file_id)
    _require_completed(record)
    query = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        query = query.where(Feature.geometry_type == geometry_type)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Feature.feature_index).limit(limit).offset(offset)).all()
    return Page[FeatureOut](
        count=total, limit=limit, offset=offset, results=[FeatureOut.model_validate(r) for r in rows]
    )


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "File not processed successfully"}},
    summary="Get measurements for the features in a file",
)
def get_measurements(
    file_id: str,
    db: DB,
    limit: Limit = 100,
    offset: Offset = 0,
    geometry_type: Annotated[str | None, Query(description="Filter, e.g. Polygon")] = None,
    measurement_status: Annotated[MeasurementStatus | None, Query(alias="status")] = None,
):
    record = _get_file(db, file_id)
    _require_completed(record)

    # Summary always covers the whole file, independent of filters/pagination.
    by_status = dict(
        db.execute(
            select(Feature.measurement_status, func.count())
            .where(Feature.file_id == file_id)
            .group_by(Feature.measurement_status)
        ).all()
    )
    totals = db.execute(
        select(func.coalesce(func.sum(Feature.area_m2), 0.0), func.coalesce(func.sum(Feature.length_m), 0.0))
        .where(Feature.file_id == file_id)
    ).one()
    summary = MeasurementSummary(
        total_features=record.feature_count,
        by_status={s: by_status.get(s, 0) for s in MeasurementStatus},
        total_area_m2=totals[0],
        total_length_m=totals[1],
    )

    query = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        query = query.where(Feature.geometry_type == geometry_type)
    if measurement_status:
        query = query.where(Feature.measurement_status == measurement_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Feature.feature_index).limit(limit).offset(offset)).all()

    return MeasurementsResponse(
        file_id=file_id,
        summary=summary,
        count=total,
        limit=limit,
        offset=offset,
        results=[MeasurementOut.model_validate(r) for r in rows],
    )


@router.delete(
    "/{file_id}/", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS, summary="Delete a file and its data"
)
def delete_file(file_id: str, db: DB):
    record = _get_file(db, file_id)
    if record.stored_path:
        shutil.rmtree(Path(record.stored_path).parent, ignore_errors=True)
    db.delete(record)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
