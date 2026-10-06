"""Pydantic models describing the public API contract."""

from datetime import UTC, datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.db.models import FileFormat, FileStatus, MeasurementStatus

T = TypeVar("T")


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; all stored timestamps are UTC, so re-attach it."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class ErrorResponse(BaseModel):
    detail: str
    code: str


class LayerInfo(BaseModel):
    name: str
    feature_count: int
    crs: str | None
    crs_assumed: bool = False


class FileDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_format: FileFormat | None
    size_bytes: int
    feature_count: int
    crs: str | None = Field(description="EPSG code (e.g. 'EPSG:4326'), 'MIXED', or null if unknown")
    crs_name: str | None
    status: FileStatus
    error: str | None = None
    warnings: list[str] = []
    layers: list[LayerInfo] = []
    geometry_types: dict[str, int] = Field(default_factory=dict, description="Feature count per geometry type")
    created_at: UTCDateTime
    processed_at: UTCDateTime | None


class FileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_format: FileFormat | None
    feature_count: int
    crs: str | None
    status: FileStatus
    created_at: UTCDateTime


class Page(BaseModel, Generic[T]):
    count: int
    limit: int
    offset: int
    results: list[T]


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_id: int = Field(validation_alias="feature_index", description="0-based index within the file")
    source_id: str | None = Field(description="Identifier from the source file (shapefile record no., KML id)")
    layer: str | None
    geometry_type: str | None
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry, coordinates in `crs`")
    crs: str | None
    properties: dict[str, Any]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_id: int = Field(validation_alias="feature_index")
    source_id: str | None
    layer: str | None
    geometry_type: str | None
    status: MeasurementStatus = Field(validation_alias="measurement_status")
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = Field(None, description="Projected CRS the measurement was computed in")
    geodesic_area_m2: float | None = Field(None, description="Ellipsoidal (WGS84) reference value")
    geodesic_length_m: float | None = Field(None, description="Ellipsoidal (WGS84) reference value")
    message: str | None = None


class MeasurementSummary(BaseModel):
    total_features: int
    by_status: dict[MeasurementStatus, int]
    total_area_m2: float
    total_length_m: float


class MeasurementsResponse(BaseModel):
    file_id: str
    units: dict[str, str] = {"area": "square_metre", "length": "metre"}
    summary: MeasurementSummary
    count: int
    limit: int
    offset: int
    results: list[MeasurementOut]
