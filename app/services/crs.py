"""CRS utilities: identifying source CRSs and choosing a projected CRS for measuring.

Strategy for measurements
-------------------------
Measuring in degrees is meaningless (a degree of longitude is ~111 km at the
equator and 0 km at the poles), so every geometry is first brought to WGS84
lon/lat and then projected into a *local, metric* CRS chosen per feature:

* **UTM** zone of the feature's centroid (EPSG:326xx north / 327xx south) when the
  feature is compact (longitude span <= ``utm_max_lon_span_deg``) and not polar.
  UTM is conformal with scale error <= ~0.04% inside a zone, which is the
  standard choice for survey-sized data and gives a reproducible EPSG code.
* Otherwise a **feature-centred azimuthal projection** built on the fly:
  Lambert Azimuthal Equal-Area for polygons (area is preserved exactly by
  definition) and Azimuthal Equidistant for lines. This covers polar features
  and features too wide for a single UTM zone.

The source CRS is never used directly for measuring, even if it is projected:
projected inputs may use non-metric units (US feet) or be heavily distorted
(Web Mercator inflates areas by 1/cos²(lat), ~2x at 45°).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from app.core.config import get_settings

WGS84 = CRS.from_epsg(4326)


@dataclass(frozen=True)
class CRSInfo:
    """A resolved source CRS plus the labels we expose through the API."""

    crs: CRS | None
    label: str | None  # "EPSG:4326", or a short name for CRSs without an EPSG code
    name: str | None
    assumed: bool = False  # True if no CRS was declared and we inferred WGS84


def describe_crs(crs: CRS, *, assumed: bool = False) -> CRSInfo:
    epsg = crs.to_epsg(min_confidence=70)
    label = f"EPSG:{epsg}" if epsg else (crs.name or "CUSTOM")[:64]
    return CRSInfo(crs=crs, label=label, name=crs.name, assumed=assumed)


def parse_prj(wkt: str) -> CRSInfo:
    """Parse the contents of a shapefile ``.prj`` (ESRI or OGC WKT)."""
    try:
        return describe_crs(CRS.from_wkt(wkt.strip()))
    except CRSError:
        # Some writers emit non-standard WKT; from_user_input is more lenient.
        return describe_crs(CRS.from_user_input(wkt.strip()))


UNKNOWN_CRS = CRSInfo(crs=None, label=None, name=None)
WGS84_INFO = describe_crs(WGS84)


def looks_geographic(bounds: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bounds
    return -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


# --------------------------------------------------------------------------- #
# Transformers
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _transformer_cached(src_wkt: str, dst_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


def get_transformer(src: CRS, dst: CRS) -> Transformer:
    """Return a cached lon/lat-ordered (``always_xy``) transformer.

    Building a Transformer costs milliseconds (PROJ database lookup) and a file
    typically reuses a handful of CRS pairs across thousands of features.
    """
    return _transformer_cached(src.to_wkt(), dst.to_wkt())


def is_wgs84_geographic(crs: CRS) -> bool:
    return crs.is_geographic and crs.to_epsg(min_confidence=70) == 4326


# --------------------------------------------------------------------------- #
# Projected CRS selection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProjectionChoice:
    crs: CRS
    label: str  # human readable, returned by the API


def utm_epsg_for(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180.0) / 6.0)) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def choose_projection(
    bounds_lonlat: tuple[float, float, float, float],
    centroid_lonlat: tuple[float, float],
    *,
    for_area: bool,
) -> ProjectionChoice:
    """Pick a metric CRS suitable for measuring a feature with the given extent."""
    settings = get_settings()
    minx, _, maxx, _ = bounds_lonlat
    lon, lat = centroid_lonlat

    if (maxx - minx) <= settings.utm_max_lon_span_deg and abs(lat) <= settings.utm_max_abs_lat_deg:
        epsg = utm_epsg_for(lon, lat)
        return ProjectionChoice(CRS.from_epsg(epsg), f"EPSG:{epsg}")

    proj = "laea" if for_area else "aeqd"
    proj4 = f"+proj={proj} +lat_0={lat:.6f} +lon_0={lon:.6f} +datum=WGS84 +units=m +no_defs"
    return ProjectionChoice(CRS.from_proj4(proj4), proj4)
