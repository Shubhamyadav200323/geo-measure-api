"""ORM models.

An ``UploadedFile`` owns many ``Feature`` rows. Measurements are computed once at
ingestion time and stored as typed columns on ``Feature`` so that reads are cheap
and totals can be aggregated in SQL.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FileStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileFormat(enum.StrEnum):
    SHAPEFILE = "SHAPEFILE"
    KML = "KML"
    KMZ = "KMZ"


class MeasurementStatus(enum.StrEnum):
    MEASURED = "MEASURED"              # area or length computed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. Point: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"        # geometry type we do not measure
    SKIPPED = "SKIPPED"                # empty/null geometry or unknown CRS
    ERROR = "ERROR"                    # unexpected failure for this feature only


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[FileFormat | None] = mapped_column(Enum(FileFormat), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    stored_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus), default=FileStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)

    crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    crs_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    layers: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.feature_index"
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True)
    feature_index: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    geometry_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, in `crs`
    crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[MeasurementStatus] = mapped_column(Enum(MeasurementStatus), index=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geodesic_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
