"""KML reader.

KML (OGC 2.2) coordinates are always WGS84 lon/lat[/alt], so every feature gets
EPSG:4326. The parser walks every ``Placemark`` in the document regardless of
nesting (``Document``/``Folder``), and understands:

* ``Point``, ``LineString``, ``LinearRing``, ``Polygon`` (with holes)
* ``MultiGeometry`` (recursively; homogeneous children become Multi* types,
  mixed children become a ``GeometryCollection``)
* ``gx:Track`` (Google extension; treated as a LineString)

Attributes come from ``name``, ``description``, ``ExtendedData/Data`` and
``ExtendedData/SchemaData/SimpleData``. Parsing uses ``defusedxml`` to block
XXE and entity-expansion ("billion laughs") attacks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.core.exceptions import InvalidGeoFileError
from app.db.models import FileFormat
from app.services.crs import WGS84_INFO
from app.services.readers.base import LayerResult, RawFeature, ReadResult

_COMMA_WS = re.compile(r"\s*,\s*")


def _local(tag: str) -> str:
    """Strip the XML namespace: '{http://www.opengis.net/kml/2.2}Point' -> 'Point'."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _child(el: Element, name: str) -> Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip()


def _parse_coords(text: str | None) -> list[list[float]]:
    if not text:
        return []
    coords = []
    for token in _COMMA_WS.sub(",", text.strip()).split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"Malformed coordinate '{token}'")
        coords.append([float(p) for p in parts[:3]])
    return coords


def _ring(el: Element | None) -> list[list[float]]:
    if el is None:
        return []
    ring_el = _child(el, "LinearRing")
    coords = _parse_coords(_text(_child(ring_el, "coordinates"))) if ring_el is not None else []
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])  # KML requires closed rings, but be lenient
    return coords


# --------------------------------------------------------------------------- #
# Geometry parsing -> GeoJSON mappings
# --------------------------------------------------------------------------- #
def _parse_geometry(el: Element) -> dict[str, Any] | None:
    kind = _local(el.tag)
    if kind == "Point":
        coords = _parse_coords(_text(_child(el, "coordinates")))
        return {"type": "Point", "coordinates": coords[0]} if coords else None
    if kind == "LineString":
        return {"type": "LineString", "coordinates": _parse_coords(_text(_child(el, "coordinates")))}
    if kind == "LinearRing":
        return {"type": "LineString", "coordinates": _parse_coords(_text(_child(el, "coordinates")))}
    if kind == "Polygon":
        outer = _ring(_child(el, "outerBoundaryIs"))
        if not outer:
            return None
        holes = [r for r in (_ring(b) for b in _children(el, "innerBoundaryIs")) if r]
        return {"type": "Polygon", "coordinates": [outer, *holes]}
    if kind == "Track":  # gx:Track
        coords = []
        for c in _children(el, "coord"):
            parts = (_text(c) or "").split()
            if len(parts) >= 2:
                coords.append([float(p) for p in parts[:3]])
        return {"type": "LineString", "coordinates": coords}
    if kind in ("MultiGeometry", "MultiTrack"):
        parts = [g for g in (_parse_geometry(c) for c in el if _local(c.tag) in GEOMETRY_TAGS) if g]
        return _combine(parts)
    if kind == "Model":
        return {"type": "Model", "coordinates": None}  # 3D model: kept but not measurable
    return None


GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Track", "MultiTrack", "Model"}

_MULTI = {"Point": "MultiPoint", "LineString": "MultiLineString", "Polygon": "MultiPolygon"}


def _flatten(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in parts:
        if p["type"].startswith("Multi"):
            single = p["type"].removeprefix("Multi")
            out.extend({"type": single, "coordinates": c} for c in p["coordinates"])
        elif p["type"] == "GeometryCollection":
            out.extend(_flatten(p["geometries"]))
        else:
            out.append(p)
    return out


def _combine(parts: list[dict[str, Any]]) -> dict[str, Any] | None:
    flat = _flatten(parts)
    if not flat:
        return None
    if len(flat) == 1:
        return flat[0]
    types = {p["type"] for p in flat}
    if len(types) == 1 and (t := types.pop()) in _MULTI:
        return {"type": _MULTI[t], "coordinates": [p["coordinates"] for p in flat]}
    return {"type": "GeometryCollection", "geometries": flat}


# --------------------------------------------------------------------------- #
# Placemarks
# --------------------------------------------------------------------------- #
def _properties(pm: Element) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for field in ("name", "description"):
        if (value := _text(_child(pm, field))) is not None:
            props[field] = value
    ext = _child(pm, "ExtendedData")
    if ext is not None:
        for data in _children(ext, "Data"):
            if key := data.get("name"):
                props[key] = _text(_child(data, "value"))
        for schema_data in _children(ext, "SchemaData"):
            for sd in _children(schema_data, "SimpleData"):
                if key := sd.get("name"):
                    props[key] = _text(sd)
    return props


def read_kml(path: Path, file_format: FileFormat = FileFormat.KML) -> ReadResult:
    try:
        root = SafeET.parse(path).getroot()
    except DefusedXmlException as exc:
        raise InvalidGeoFileError("KML contains forbidden XML constructs (DTD/entities).") from exc
    except SafeET.ParseError as exc:
        raise InvalidGeoFileError(f"File is not well-formed XML: {exc}") from exc

    if _local(root.tag) != "kml":
        raise InvalidGeoFileError(f"Root element is <{_local(root.tag)}>, expected <kml>.")

    result = ReadResult(file_format=file_format)
    layer = LayerResult(name=path.stem, crs=WGS84_INFO)

    for index, pm in enumerate(el for el in root.iter() if _local(el.tag) == "Placemark"):
        geometry = None
        geom_el = next((c for c in pm if _local(c.tag) in GEOMETRY_TAGS), None)
        if geom_el is not None:
            try:
                geometry = _parse_geometry(geom_el)
            except ValueError as exc:
                result.warnings.append(f"Placemark {index}: invalid coordinates ({exc}); geometry dropped.")
        result.features.append(
            RawFeature(
                index=index,
                geometry=geometry,
                properties=_properties(pm),
                crs=WGS84_INFO,
                source_id=pm.get("id"),
            )
        )
        layer.feature_count += 1

    result.layers.append(layer)
    return result
