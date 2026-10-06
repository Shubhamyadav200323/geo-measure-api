"""Common data structures produced by every reader.

Readers only *parse*: they turn a file into ``RawFeature`` objects carrying a
GeoJSON-like geometry in the source CRS. They know nothing about measurement or
persistence, which keeps each format isolated and easy to test.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import math
from dataclasses import dataclass, field
from typing import Any

from app.db.models import FileFormat
from app.services.crs import CRSInfo


@dataclass
class RawFeature:
    index: int
    geometry: dict[str, Any] | None  # GeoJSON geometry mapping, or None for null shapes
    properties: dict[str, Any]
    crs: CRSInfo
    source_id: str | None = None
    layer: str | None = None


@dataclass
class LayerResult:
    name: str
    crs: CRSInfo
    feature_count: int = 0


@dataclass
class ReadResult:
    file_format: FileFormat
    features: list[RawFeature] = field(default_factory=list)
    layers: list[LayerResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def json_safe(value: Any) -> Any:
    """Coerce attribute values (dates, decimals, bytes, NaN) into JSON-serialisable types."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)
