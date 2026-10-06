import pytest

from tests.factories import SAMPLE_KML, SQUARE_LONLAT, SQUARE_UTM_1KM, build_shapefile_zip


@pytest.fixture
def kml_file(upload):
    response = upload("survey.kml", SAMPLE_KML.encode())
    assert response.status_code == 201, response.text
    return response.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


class TestUpload:
    def test_kml_upload_returns_file_info(self, kml_file):
        assert kml_file["filename"] == "survey.kml"
        assert kml_file["file_format"] == "KML"
        assert kml_file["feature_count"] == 8
        assert kml_file["crs"] == "EPSG:4326"
        assert kml_file["status"] == "COMPLETED"
        assert kml_file["geometry_types"]["Polygon"] == 2

    def test_shapefile_upload(self, upload):
        data = build_shapefile_zip([({"type": "Polygon", "coordinates": [SQUARE_UTM_1KM]}, {"id": "1"})], epsg=32643)
        body = upload("parcels.zip", data).json()
        assert body["status"] == "COMPLETED"
        assert body["file_format"] == "SHAPEFILE"
        assert body["crs"] == "EPSG:32643"

    def test_unsupported_type_is_415(self, upload):
        response = upload("data.csv", b"a,b\n1,2")
        assert response.status_code == 415
        assert response.json()["code"] == "unsupported_file_type"

    def test_corrupt_file_is_recorded_as_failed(self, client, upload):
        response = upload("broken.kml", b"<kml><Placemark>")
        assert response.status_code == 422
        body = response.json()
        assert body["status"] == "FAILED"
        assert "well-formed" in body["error"]
        # The failure is persisted and inspectable
        assert client.get(f"/api/files/{body['id']}/").json()["status"] == "FAILED"
        assert client.get(f"/api/files/{body['id']}/measurements/").status_code == 409

    def test_empty_file(self, upload):
        assert upload("empty.kml", b"").status_code == 422

    def test_filename_path_is_sanitised(self, upload):
        body = upload("../../etc/survey.kml", SAMPLE_KML.encode()).json()
        assert body["filename"] == "survey.kml"

    def test_missing_file_field(self, client):
        assert client.post("/api/files/").status_code == 422


class TestRead:
    def test_get_file(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/").json()
        assert body["id"] == kml_file["id"]
        assert body["feature_count"] == 8

    def test_get_unknown_file(self, client):
        response = client.get("/api/files/doesnotexist/")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_list_files(self, client, kml_file):
        body = client.get("/api/files/").json()
        assert body["count"] >= 1
        assert any(f["id"] == kml_file["id"] for f in body["results"])

    def test_features_include_geometry_crs_properties(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/features/", params={"limit": 2}).json()
        assert body["count"] == 8
        assert len(body["results"]) == 2
        first = body["results"][0]
        assert first["feature_id"] == 0
        assert first["geometry_type"] == "Polygon"
        assert first["geometry"]["type"] == "Polygon"
        assert first["crs"] == "EPSG:4326"
        assert first["properties"]["owner"] == "Sharma"


class TestMeasurements:
    def test_measurements(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/measurements/").json()
        assert body["count"] == 8
        summary = body["summary"]
        assert summary["by_status"] == {
            "MEASURED": 5, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1, "SKIPPED": 1, "ERROR": 0,
        }
        by_source = {m["source_id"]: m for m in body["results"]}
        assert by_source["parcel-1"]["area_m2"] == pytest.approx(1_086_000, rel=1e-3)
        assert by_source["parcel-1"]["projected_crs"] == "EPSG:32643"
        assert by_source["road-1"]["length_m"] > 0
        assert by_source["well-1"]["status"] == "NOT_APPLICABLE"
        assert by_source["mixed-1"]["status"] == "UNSUPPORTED"
        assert summary["total_area_m2"] == pytest.approx(
            sum(m["area_m2"] or 0 for m in body["results"])
        )

    def test_filter_by_geometry_type(self, client, kml_file):
        body = client.get(
            f"/api/files/{kml_file['id']}/measurements/", params={"geometry_type": "LineString"}
        ).json()
        assert body["count"] == 2
        assert body["summary"]["total_features"] == 8  # summary is file-wide

    def test_filter_by_status(self, client, kml_file):
        body = client.get(f"/api/files/{kml_file['id']}/measurements/", params={"status": "MEASURED"}).json()
        assert {m["status"] for m in body["results"]} == {"MEASURED"}

    def test_projected_and_geographic_inputs_agree(self, client, upload):
        """The same 1 km² square, delivered in UTM and in lon/lat, measures the same."""
        from pyproj import Transformer

        to_ll = Transformer.from_crs(32643, 4326, always_xy=True)
        square_ll = [list(to_ll.transform(x, y)) for x, y in SQUARE_UTM_1KM]
        results = []
        for epsg, coords in ((32643, SQUARE_UTM_1KM), (4326, square_ll)):
            data = build_shapefile_zip([({"type": "Polygon", "coordinates": [coords]}, {})], epsg=epsg)
            fid = upload(f"sq_{epsg}.zip", data).json()["id"]
            results.append(client.get(f"/api/files/{fid}/measurements/").json()["results"][0]["area_m2"])
        assert results[0] == pytest.approx(1_000_000, rel=1e-6)
        assert results[1] == pytest.approx(results[0], rel=1e-6)

    def test_web_mercator_is_not_measured_naively(self, client, upload):
        """EPSG:3857 inflates areas ~1.29x at 28°N; we must reproject, not use its units."""
        from pyproj import Transformer

        to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
        coords = [list(to_merc.transform(x, y)) for x, y in SQUARE_LONLAT]
        data = build_shapefile_zip([({"type": "Polygon", "coordinates": [coords]}, {})], epsg=3857)
        fid = upload("merc.zip", data).json()["id"]
        area = client.get(f"/api/files/{fid}/measurements/").json()["results"][0]["area_m2"]
        assert area == pytest.approx(1_086_000, rel=1e-3)


def test_delete(client, upload):
    fid = upload("survey.kml", SAMPLE_KML.encode()).json()["id"]
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    assert client.get(f"/api/files/{fid}/").status_code == 404
