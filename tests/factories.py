"""Builders for test fixtures and sample data (shapefile ZIPs, KML documents)."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS

# A 0.01° x 0.01° square near Gurugram, India (lon 77.0, lat 28.4)
SQUARE_LONLAT = [[77.0, 28.4], [77.01, 28.4], [77.01, 28.41], [77.0, 28.41], [77.0, 28.4]]
# A 1 km x 1 km square in UTM zone 43N (EPSG:32643), same region
SQUARE_UTM_1KM = [[700000, 3145000], [701000, 3145000], [701000, 3146000], [700000, 3146000], [700000, 3145000]]


def esri_wkt(epsg: int) -> str:
    return CRS.from_epsg(epsg).to_wkt("WKT1_ESRI")


def build_shapefile_zip(
    features: list[tuple[dict[str, Any] | None, dict[str, Any]]],
    *,
    name: str = "layer",
    epsg: int | None = 4326,
    folder: str = "",
    extra_files: dict[str, bytes] | None = None,
    omit: tuple[str, ...] = (),
) -> bytes:
    """Build a zipped shapefile from (GeoJSON geometry | None, attributes) pairs.

    All geometries must share a family (points / lines / polygons); ``None``
    produces a null shape.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / name
        first = next((g for g, _ in features if g), None)
        shape_type = {
            "Point": shapefile.POINT,
            "MultiPoint": shapefile.MULTIPOINT,
            "LineString": shapefile.POLYLINE,
            "MultiLineString": shapefile.POLYLINE,
            "Polygon": shapefile.POLYGON,
            "MultiPolygon": shapefile.POLYGON,
        }[first["type"]] if first else shapefile.POLYGON

        field_names = sorted({k for _, props in features for k in props}) or ["fid"]
        with shapefile.Writer(str(base), shapeType=shape_type) as w:
            for fname in field_names:
                w.field(fname, "C", size=80)
            for geom, props in features:
                if geom is None:
                    w.null()
                else:
                    w.shape(geom)
                w.record(*[str(props.get(f, "")) for f in field_names])

        if epsg is not None:
            (Path(tmp) / f"{name}.prj").write_text(esri_wkt(epsg))
        (Path(tmp) / f"{name}.cpg").write_text("UTF-8")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(Path(tmp).iterdir()):
                if path.suffix.lower() in omit:
                    continue
                zf.write(path, f"{folder}{path.name}")
            for arcname, data in (extra_files or {}).items():
                zf.writestr(arcname, data)
        return buf.getvalue()


def coords_text(coords: list[list[float]]) -> str:
    return " ".join(",".join(str(c) for c in pt) for pt in coords)


SAMPLE_KML = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <name>Survey</name>
    <Folder>
      <name>Parcels</name>
      <Placemark id="parcel-1">
        <name>Parcel A</name>
        <ExtendedData>
          <Data name="owner"><value>Sharma</value></Data>
          <Data name="landuse"><value>agriculture</value></Data>
        </ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            {coords_text(SQUARE_LONLAT)}
          </coordinates></LinearRing></outerBoundaryIs>
        </Polygon>
      </Placemark>
      <Placemark id="parcel-2">
        <name>Parcel B (with courtyard)</name>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            77.02,28.40,0 77.03,28.40,0 77.03,28.41,0 77.02,28.41,0 77.02,28.40,0
          </coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>
            77.024,28.404,0 77.026,28.404,0 77.026,28.406,0 77.024,28.406,0 77.024,28.404,0
          </coordinates></LinearRing></innerBoundaryIs>
        </Polygon>
      </Placemark>
    </Folder>
    <Placemark id="road-1">
      <name>Access road</name>
      <description>Gravel road along the parcels</description>
      <LineString><coordinates>77.0,28.395 77.01,28.395 77.03,28.395</coordinates></LineString>
    </Placemark>
    <Placemark id="well-1">
      <name>Well</name>
      <Point><coordinates>77.005,28.405,0</coordinates></Point>
    </Placemark>
    <Placemark id="mixed-1">
      <name>Gate and fence</name>
      <MultiGeometry>
        <Point><coordinates>77.0,28.41</coordinates></Point>
        <LineString><coordinates>77.0,28.41 77.01,28.41</coordinates></LineString>
      </MultiGeometry>
    </Placemark>
    <Placemark id="plots">
      <name>Twin plots</name>
      <MultiGeometry>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          77.04,28.40 77.045,28.40 77.045,28.405 77.04,28.405 77.04,28.40
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          77.05,28.40 77.055,28.40 77.055,28.405 77.05,28.405 77.05,28.40
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
      </MultiGeometry>
    </Placemark>
    <Placemark id="track-1">
      <name>Walked boundary</name>
      <gx:Track>
        <when>2026-01-01T10:00:00Z</when><when>2026-01-01T10:05:00Z</when>
        <gx:coord>77.0 28.39 0</gx:coord><gx:coord>77.01 28.39 0</gx:coord>
      </gx:Track>
    </Placemark>
    <Placemark id="empty-1"><name>No geometry</name></Placemark>
  </Document>
</kml>
"""
