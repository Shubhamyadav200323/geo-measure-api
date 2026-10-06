"""Per-feature measurement.

Flow for one geometry::

    source CRS ──(to WGS84)──► lon/lat ──(choose local metric CRS)──► projected
                                  │                                      │
                                  └─► geodesic reference values          └─► area / length

Every failure is contained to the feature it happens on: the function never
raises for bad data, it returns a result with an appropriate status instead.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import shapely
from pyproj import CRS, Geod
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.db.models import MeasurementStatus
from app.services.crs import WGS84, choose_projection, get_transformer, is_wgs84_geographic

logger = logging.getLogger(__name__)

GEOD = Geod(ellps="WGS84")

AREAL_TYPES = {"Polygon", "MultiPolygon"}
LINEAR_TYPES = {"LineString", "MultiLineString", "LinearRing"}
PUNTAL_TYPES = {"Point", "MultiPoint"}

# Max edge length before reprojection (~1.1 km at the equator / 1 km)
DENSIFY_DEG = 0.01
DENSIFY_PROJECTED_UNITS = 1000.0


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    message: str | None = None


def _to_lonlat(geom: BaseGeometry, src_crs: CRS) -> BaseGeometry:
    if is_wgs84_geographic(src_crs):
        return geom
    return transform(get_transformer(src_crs, WGS84).transform, geom)


def _fix_polygonal(geom: BaseGeometry) -> tuple[BaseGeometry, str | None]:
    """Repair invalid polygons (self-intersections, bow-ties, bad ring order)."""
    if geom.is_valid:
        return geom, None
    reason = shapely.is_valid_reason(geom)
    fixed = shapely.make_valid(geom)
    if fixed.geom_type == "GeometryCollection":  # keep only the areal parts
        polys = [g for g in fixed.geoms if g.geom_type in AREAL_TYPES]
        fixed = shapely.union_all(polys) if polys else shapely.Polygon()
    return fixed, f"Invalid polygon repaired before measuring ({reason})."


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS | None) -> MeasurementResult:
    if geom is None or geom.is_empty:
        return MeasurementResult(MeasurementStatus.SKIPPED, message="Feature has no geometry.")

    gtype = geom.geom_type
    if gtype in PUNTAL_TYPES:
        return MeasurementResult(MeasurementStatus.NOT_APPLICABLE, message="Points have no area or length.")
    if gtype not in AREAL_TYPES | LINEAR_TYPES:
        return MeasurementResult(
            MeasurementStatus.UNSUPPORTED,
            message=f"Measurement is not supported for geometry type '{gtype}'.",
        )
    if src_crs is None:
        return MeasurementResult(
            MeasurementStatus.SKIPPED,
            message="Source CRS is unknown (no .prj and coordinates are not lon/lat); cannot measure reliably.",
        )

    try:
        return _measure(shapely.force_2d(geom), src_crs, gtype)
    except Exception as exc:  # noqa: BLE001 - isolate per-feature failures
        logger.exception("Measurement failed for %s geometry", gtype)
        return MeasurementResult(MeasurementStatus.ERROR, message=f"Measurement failed: {exc}")


def _measure(geom: BaseGeometry, src_crs: CRS, gtype: str) -> MeasurementResult:
    notes: list[str] = []
    is_areal = gtype in AREAL_TYPES

    if is_areal:
        geom, note = _fix_polygonal(geom)
        if note:
            notes.append(note)

    # Densify long edges in the *source* CRS first. Only vertices are reprojected,
    # so without this a long edge that is straight in the source CRS (e.g. a line
    # of latitude) becomes a different straight chord in the target CRS, which
    # biases areas of large features by several percent.
    geom = shapely.segmentize(geom, DENSIFY_DEG if src_crs.is_geographic else DENSIFY_PROJECTED_UNITS)

    lonlat = _to_lonlat(geom, src_crs)
    minx, miny, maxx, maxy = lonlat.bounds
    if not all(map(math.isfinite, (minx, miny, maxx, maxy))):
        return MeasurementResult(MeasurementStatus.ERROR, message="Coordinates could not be transformed to WGS84.")
    if maxx - minx > 180:
        notes.append("Feature spans >180° of longitude (antimeridian crossing?); results may be unreliable.")

    centroid = lonlat.centroid
    choice = choose_projection(lonlat.bounds, (centroid.x, centroid.y), for_area=is_areal)
    projected = transform(get_transformer(WGS84, choice.crs).transform, lonlat)

    result = MeasurementResult(MeasurementStatus.MEASURED, projected_crs=choice.label)
    if is_areal:
        result.area_m2 = projected.area
        result.perimeter_m = projected.length
        # pyproj's geodesic area is signed by ring orientation; normalise so that
        # holes (clockwise) are subtracted from shells (counter-clockwise).
        geod_area, _ = GEOD.geometry_area_perimeter(shapely.orient_polygons(lonlat, exterior_cw=False))
        result.geodesic_area_m2 = abs(geod_area)
    else:
        result.length_m = projected.length
        result.geodesic_length_m = GEOD.geometry_length(lonlat)

    result.message = " ".join(notes) or None
    return result

