import io
import zipfile
from pathlib import Path

import pytest

from app.core.exceptions import InvalidGeoFileError, UnsupportedFileTypeError
from app.db.models import FileFormat
from app.services.readers import read_geo_file
from tests.factories import SAMPLE_KML, SQUARE_LONLAT, SQUARE_UTM_1KM, build_shapefile_zip

POLY = {"type": "Polygon", "coordinates": [SQUARE_LONLAT]}


def write(tmp_path: Path, name: str, content: bytes | str) -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def read(tmp_path: Path, name: str, content: bytes | str):
    work = tmp_path / "work"
    work.mkdir()
    return read_geo_file(write(tmp_path, name, content), name, work)


class TestKML:
    def test_parses_all_placemarks_with_attributes(self, tmp_path):
        result = read(tmp_path, "survey.kml", SAMPLE_KML)
        assert result.file_format is FileFormat.KML
        assert len(result.features) == 8
        first = result.features[0]
        assert first.source_id == "parcel-1"
        assert first.crs.label == "EPSG:4326"
        assert first.properties == {"name": "Parcel A", "owner": "Sharma", "landuse": "agriculture"}

    def test_geometry_types(self, tmp_path):
        types = [f.geometry and f.geometry["type"] for f in read(tmp_path, "s.kml", SAMPLE_KML).features]
        assert types == [
            "Polygon", "Polygon", "LineString", "Point", "GeometryCollection", "MultiPolygon", "LineString", None,
        ]

    def test_polygon_holes(self, tmp_path):
        parcel_b = read(tmp_path, "s.kml", SAMPLE_KML).features[1]
        assert len(parcel_b.geometry["coordinates"]) == 2  # shell + hole

    def test_kml_without_namespace(self, tmp_path):
        kml = "<kml><Placemark><Point><coordinates>1,2</coordinates></Point></Placemark></kml>"
        assert read(tmp_path, "a.kml", kml).features[0].geometry == {"type": "Point", "coordinates": [1.0, 2.0]}

    def test_rejects_non_kml_xml(self, tmp_path):
        with pytest.raises(InvalidGeoFileError, match="expected <kml>"):
            read(tmp_path, "a.kml", "<gpx></gpx>")

    def test_rejects_malformed_xml(self, tmp_path):
        with pytest.raises(InvalidGeoFileError, match="well-formed"):
            read(tmp_path, "a.kml", "<kml><Placemark>")

    def test_blocks_entity_expansion(self, tmp_path):
        evil = '<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa">]><kml>&a;</kml>'
        with pytest.raises(InvalidGeoFileError, match="forbidden"):
            read(tmp_path, "a.kml", evil)

    def test_bad_coordinates_drop_geometry_with_warning(self, tmp_path):
        kml = "<kml><Placemark><Point><coordinates>abc</coordinates></Point></Placemark></kml>"
        result = read(tmp_path, "a.kml", kml)
        assert result.features[0].geometry is None
        assert result.warnings

    def test_kmz(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.kml", SAMPLE_KML)
        result = read(tmp_path, "survey.kmz", buf.getvalue())
        assert result.file_format is FileFormat.KMZ
        assert len(result.features) == 8


class TestShapefile:
    def test_reads_features_crs_and_properties(self, tmp_path):
        data = build_shapefile_zip([(POLY, {"name": "Parcel A", "owner": "Sharma"})], epsg=4326)
        result = read(tmp_path, "parcels.zip", data)
        assert result.file_format is FileFormat.SHAPEFILE
        (feature,) = result.features
        assert feature.geometry["type"] == "Polygon"
        assert feature.crs.label == "EPSG:4326"
        assert feature.properties == {"name": "Parcel A", "owner": "Sharma"}

    def test_projected_crs_detected(self, tmp_path):
        data = build_shapefile_zip([({"type": "Polygon", "coordinates": [SQUARE_UTM_1KM]}, {})], epsg=32643)
        assert read(tmp_path, "utm.zip", data).features[0].crs.label == "EPSG:32643"

    def test_shapefile_in_subfolder(self, tmp_path):
        data = build_shapefile_zip([(POLY, {})], folder="export/data/")
        assert len(read(tmp_path, "nested.zip", data).features) == 1

    def test_missing_prj_with_lonlat_assumes_wgs84(self, tmp_path):
        result = read(tmp_path, "noprj.zip", build_shapefile_zip([(POLY, {})], epsg=None))
        assert result.features[0].crs.label == "EPSG:4326"
        assert result.features[0].crs.assumed
        assert any("assumed" in w for w in result.warnings)

    def test_missing_prj_with_projected_coords_is_unknown(self, tmp_path):
        data = build_shapefile_zip([({"type": "Polygon", "coordinates": [SQUARE_UTM_1KM]}, {})], epsg=None)
        result = read(tmp_path, "noprj.zip", data)
        assert result.features[0].crs.crs is None

    def test_null_shapes(self, tmp_path):
        result = read(tmp_path, "n.zip", build_shapefile_zip([(POLY, {}), (None, {})]))
        assert result.features[1].geometry is None

    def test_missing_dbf(self, tmp_path):
        with pytest.raises(InvalidGeoFileError, match=r"\.dbf"):
            read(tmp_path, "x.zip", build_shapefile_zip([(POLY, {})], omit=(".dbf",)))

    def test_multiple_layers(self, tmp_path):
        a = zipfile.ZipFile(io.BytesIO(build_shapefile_zip([(POLY, {})], name="parcels")))
        line = {"type": "LineString", "coordinates": [[77, 28], [77.1, 28]]}
        b = zipfile.ZipFile(io.BytesIO(build_shapefile_zip([(line, {})], name="roads")))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for src in (a, b):
                for n in src.namelist():
                    out.writestr(n, src.read(n))
        result = read(tmp_path, "multi.zip", buf.getvalue())
        assert [layer.name for layer in result.layers] == ["parcels", "roads"]
        assert [f.layer for f in result.features] == ["parcels", "roads"]


class TestValidation:
    def test_unsupported_extension(self, tmp_path):
        with pytest.raises(UnsupportedFileTypeError):
            read(tmp_path, "data.geojson", "{}")

    def test_zip_without_geodata(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "hello")
        with pytest.raises(InvalidGeoFileError, match="no Shapefile"):
            read(tmp_path, "x.zip", buf.getvalue())

    def test_fake_zip(self, tmp_path):
        with pytest.raises(InvalidGeoFileError, match="not a ZIP"):
            read(tmp_path, "x.zip", "definitely not a zip")

    def test_zip_slip_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.shp", "x")
        with pytest.raises(InvalidGeoFileError, match="escapes"):
            read(tmp_path, "x.zip", buf.getvalue())
