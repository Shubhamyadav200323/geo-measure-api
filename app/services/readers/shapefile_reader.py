"""Shapefile reader (pure Python, via ``pyshp``).

A shapefile is a set of sidecar files sharing a basename: ``.shp`` (geometry),
``.shx`` (index), ``.dbf`` (attributes), and optionally ``.prj`` (CRS) and
``.cpg`` (attribute encoding). An uploaded ZIP may contain several shapefiles,
possibly in sub-folders; each one is read as a separate *layer*.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import shapefile  # pyshp

from app.core.exceptions import InvalidGeoFileError
from app.db.models import FileFormat
from app.services.crs import UNKNOWN_CRS, WGS84_INFO, CRSInfo, describe_crs, looks_geographic, parse_prj
from app.services.readers.base import LayerResult, RawFeature, ReadResult, json_safe

REQUIRED_SIDECARS = (".shx", ".dbf")


def _sidecar(shp: Path, ext: str) -> Path | None:
    """Find a sidecar regardless of extension case (``.DBF`` vs ``.dbf``)."""
    for candidate in shp.parent.iterdir():
        if candidate.stem == shp.stem and candidate.suffix.lower() == ext:
            return candidate
    return None


def _encoding_for(shp: Path) -> str:
    cpg = _sidecar(shp, ".cpg")
    if cpg:
        declared = cpg.read_text(errors="ignore").strip()
        if declared.isdigit():  # e.g. "1252" -> cp1252
            declared = f"cp{declared}"
        try:
            return codecs.lookup(declared).name
        except LookupError:
            pass
    return "utf-8"


def _crs_for(shp: Path, reader: shapefile.Reader, warnings: list[str]) -> CRSInfo:
    prj = _sidecar(shp, ".prj")
    if prj:
        try:
            return parse_prj(prj.read_text(errors="ignore"))
        except Exception:  # noqa: BLE001 - fall through to inference
            warnings.append(f"Layer '{shp.stem}': could not parse .prj file.")

    if reader.numRecords and reader.shapeType != shapefile.NULL and looks_geographic(tuple(reader.bbox)):
        warnings.append(
            f"Layer '{shp.stem}': no usable .prj file; coordinates look like lon/lat so EPSG:4326 was assumed."
        )
        return describe_crs(WGS84_INFO.crs, assumed=True)

    warnings.append(f"Layer '{shp.stem}': no usable .prj file and coordinates are not lon/lat; CRS is unknown.")
    return UNKNOWN_CRS


def read_shapefiles(shp_paths: list[Path]) -> ReadResult:
    result = ReadResult(file_format=FileFormat.SHAPEFILE)
    index = 0
    multi_layer = len(shp_paths) > 1

    for shp in sorted(shp_paths):
        layer_name = shp.stem
        missing = [ext for ext in REQUIRED_SIDECARS if _sidecar(shp, ext) is None]
        if missing:
            raise InvalidGeoFileError(
                f"Shapefile '{shp.name}' is missing required component(s): {', '.join(missing)}."
            )

        try:
            reader = shapefile.Reader(str(shp.with_suffix("")), encoding=_encoding_for(shp), encodingErrors="replace")
        except Exception as exc:  # pyshp raises a variety of exception types
            raise InvalidGeoFileError(f"Could not read shapefile '{shp.name}': {exc}") from exc

        with reader:
            crs = _crs_for(shp, reader, result.warnings)
            layer = LayerResult(name=layer_name, crs=crs)
            field_names = [f[0] for f in reader.fields[1:]]  # first field is the DeletionFlag

            for record_no, shape_rec in enumerate(_iter_records(reader, shp.name)):
                shape, record = shape_rec
                geometry = None
                if shape is not None and shape.shapeType != shapefile.NULL:
                    geometry = shape.__geo_interface__
                properties = {name: json_safe(value) for name, value in zip(field_names, record, strict=False)}
                result.features.append(
                    RawFeature(
                        index=index,
                        geometry=geometry,
                        properties=properties,
                        crs=crs,
                        source_id=str(record_no),
                        layer=layer_name if multi_layer else None,
                    )
                )
                index += 1
                layer.feature_count += 1
            result.layers.append(layer)

    return result


def _iter_records(reader: shapefile.Reader, name: str):
    try:
        for sr in reader.iterShapeRecords():
            yield sr.shape, list(sr.record)
    except Exception as exc:  # noqa: BLE001
        raise InvalidGeoFileError(f"Shapefile '{name}' is corrupt or truncated: {exc}") from exc
