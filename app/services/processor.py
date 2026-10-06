"""File ingestion pipeline: store -> parse -> measure -> persist.

This module is deliberately independent of FastAPI so it can be moved behind a
task queue (Celery/RQ/arq) without changes: a worker would call
``process_file(db, file_id)`` exactly as the request handler does today.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import shapely.geometry
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import FileTooLargeError, GeoServiceError, InvalidGeoFileError
from app.db.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.services.measurement import MeasurementResult, measure_geometry
from app.services.readers import read_geo_file, validate_extension
from app.services.readers.base import RawFeature

logger = logging.getLogger(__name__)

CHUNK = 1 << 20


def _safe_name(filename: str) -> str:
    """Basename only, so a client-supplied name can never traverse directories."""
    return Path(filename.replace("\\", "/")).name or "upload"


def store_upload(db: Session, stream: BinaryIO, filename: str) -> UploadedFile:
    """Validate and stream an upload to disk, creating a PENDING record."""
    settings = get_settings()
    filename = _safe_name(filename)
    validate_extension(filename)

    record = UploadedFile(filename=filename, status=FileStatus.PENDING, warnings=[], layers=[])
    db.add(record)
    db.flush()  # assigns the id

    dest_dir = settings.upload_dir / record.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise FileTooLargeError(f"Upload exceeds the {settings.max_upload_size_mb} MB limit.")
                out.write(chunk)
    except Exception:
        db.rollback()
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise

    if size == 0:
        db.rollback()
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise InvalidGeoFileError("Uploaded file is empty.")

    record.size_bytes = size
    record.stored_path = str(dest)
    db.commit()
    return record


def process_file(db: Session, record: UploadedFile) -> UploadedFile:
    """Parse and measure a stored upload. Never raises for bad input: failures are
    recorded on the file (status FAILED + error) so they are visible via the API."""
    record.status = FileStatus.PROCESSING
    db.commit()

    try:
        with tempfile.TemporaryDirectory(prefix="geo-") as tmp:
            parsed = read_geo_file(Path(record.stored_path), record.filename, Path(tmp))
            features = [_build_feature(record.id, raw) for raw in parsed.features]
    except GeoServiceError as exc:
        return _fail(db, record, exc.message)
    except Exception as exc:  # noqa: BLE001 - a bug must not leave the file stuck in PROCESSING
        logger.exception("Unexpected error processing file %s", record.id)
        return _fail(db, record, f"Unexpected error while processing file: {exc.__class__.__name__}")

    crs_labels = {layer.crs.label for layer in parsed.layers if layer.feature_count}
    record.file_format = parsed.file_format
    record.feature_count = len(features)
    record.warnings = parsed.warnings
    record.layers = [
        {
            "name": layer.name,
            "feature_count": layer.feature_count,
            "crs": layer.crs.label,
            "crs_assumed": layer.crs.assumed,
        }
        for layer in parsed.layers
    ]
    if len(crs_labels) > 1:
        record.crs, record.crs_name = "MIXED", "Layers use different CRSs; see `layers`"
    elif parsed.layers:
        first = next((lyr for lyr in parsed.layers if lyr.feature_count), parsed.layers[0])
        record.crs, record.crs_name = first.crs.label, first.crs.name
    if not features:
        record.warnings.append("File contains no features.")

    db.add_all(features)
    record.status = FileStatus.COMPLETED
    record.processed_at = datetime.now(UTC)
    db.commit()
    logger.info("Processed file %s: %d features (%s)", record.id, len(features), _status_counts(features))
    return record


def _fail(db: Session, record: UploadedFile, message: str) -> UploadedFile:
    db.rollback()
    record.status = FileStatus.FAILED
    record.error = message
    record.processed_at = datetime.now(UTC)
    db.commit()
    return record


def _build_feature(file_id: str, raw: RawFeature) -> Feature:
    geometry_type = raw.geometry.get("type") if raw.geometry else None
    geom = None
    measurement: MeasurementResult | None = None

    if raw.geometry:
        try:
            geom = shapely.geometry.shape(raw.geometry)
        except Exception as exc:  # noqa: BLE001 - malformed or exotic geometry
            status = (
                MeasurementStatus.UNSUPPORTED
                if geometry_type not in _SHAPELY_TYPES
                else MeasurementStatus.ERROR
            )
            message = (
                f"Measurement is not supported for geometry type '{geometry_type}'."
                if status is MeasurementStatus.UNSUPPORTED
                else f"Invalid geometry: {exc}"
            )
            measurement = MeasurementResult(status, message=message)

    if measurement is None:
        measurement = measure_geometry(geom, raw.crs.crs)

    return Feature(
        file_id=file_id,
        feature_index=raw.index,
        source_id=raw.source_id,
        layer=raw.layer,
        geometry_type=geometry_type,
        geometry=raw.geometry if geometry_type in _SHAPELY_TYPES else None,
        crs=raw.crs.label,
        properties=raw.properties,
        measurement_status=measurement.status,
        area_m2=measurement.area_m2,
        perimeter_m=measurement.perimeter_m,
        length_m=measurement.length_m,
        projected_crs=measurement.projected_crs,
        geodesic_area_m2=measurement.geodesic_area_m2,
        geodesic_length_m=measurement.geodesic_length_m,
        message=measurement.message,
    )


_SHAPELY_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString", "LinearRing",
    "Polygon", "MultiPolygon", "GeometryCollection",
}


def _status_counts(features: list[Feature]) -> dict[str, int]:
    return dict(Counter(f.measurement_status.value for f in features))
