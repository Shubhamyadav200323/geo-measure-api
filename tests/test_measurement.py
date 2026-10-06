import pytest
import shapely
from pyproj import CRS

from app.db.models import MeasurementStatus
from app.services.crs import WGS84, choose_projection, utm_epsg_for
from app.services.measurement import measure_geometry
from tests.factories import SQUARE_LONLAT, SQUARE_UTM_1KM

UTM43N = CRS.from_epsg(32643)


def rel(a: float, b: float) -> float:
    return abs(a - b) / b


class TestProjectionChoice:
    @pytest.mark.parametrize(
        ("lon", "lat", "epsg"),
        [(77.0, 28.4, 32643), (-0.1, 51.5, 32630), (151.2, -33.9, 32756), (-180.0, 0.0, 32601), (179.99, 1, 32660)],
    )
    def test_utm_zone(self, lon, lat, epsg):
        assert utm_epsg_for(lon, lat) == epsg

    def test_compact_feature_uses_utm(self):
        choice = choose_projection((77.0, 28.4, 77.1, 28.5), (77.05, 28.45), for_area=True)
        assert choice.label == "EPSG:32643"

    def test_wide_polygon_uses_equal_area(self):
        choice = choose_projection((60.0, 10.0, 90.0, 30.0), (75.0, 20.0), for_area=True)
        assert "+proj=laea" in choice.label

    def test_wide_line_uses_equidistant(self):
        choice = choose_projection((60.0, 10.0, 90.0, 30.0), (75.0, 20.0), for_area=False)
        assert "+proj=aeqd" in choice.label

    def test_polar_feature_avoids_utm(self):
        choice = choose_projection((10.0, 85.0, 11.0, 86.0), (10.5, 85.5), for_area=True)
        assert "+proj=laea" in choice.label


class TestMeasurements:
    def test_projected_square_round_trips_exactly(self):
        result = measure_geometry(shapely.Polygon(SQUARE_UTM_1KM), UTM43N)
        assert result.status is MeasurementStatus.MEASURED
        assert result.area_m2 == pytest.approx(1_000_000, rel=1e-9)
        assert result.perimeter_m == pytest.approx(4_000, rel=1e-9)

    def test_geographic_polygon_is_not_measured_in_degrees(self):
        result = measure_geometry(shapely.Polygon(SQUARE_LONLAT), WGS84)
        # 0.01° x 0.01° at 28.4°N is ~1.086 km²; in degrees it would be 0.0001.
        assert result.area_m2 == pytest.approx(1_086_000, rel=1e-3)
        assert rel(result.area_m2, result.geodesic_area_m2) < 5e-4
        assert result.projected_crs == "EPSG:32643"

    def test_polygon_hole_is_subtracted(self):
        shell = [(77.02, 28.40), (77.03, 28.40), (77.03, 28.41), (77.02, 28.41)]
        hole = [(77.024, 28.404), (77.026, 28.404), (77.026, 28.406), (77.024, 28.406)]  # same winding as shell
        full = measure_geometry(shapely.Polygon(shell), WGS84)
        holed = measure_geometry(shapely.Polygon(shell, [hole]), WGS84)
        assert holed.area_m2 < full.area_m2
        assert rel(holed.area_m2, holed.geodesic_area_m2) < 5e-4

    def test_line_length_along_equator(self):
        # One degree of longitude on the WGS84 equator is 111,319.49 m.
        result = measure_geometry(shapely.LineString([(0, 0), (1, 0)]), WGS84)
        assert result.geodesic_length_m == pytest.approx(111_319.49, rel=1e-6)
        assert rel(result.length_m, 111_319.49) < 1e-3  # UTM scale error is at most ~0.04%

    def test_multilinestring_lengths_are_summed(self):
        geom = shapely.MultiLineString([[(77.0, 28.4), (77.01, 28.4)], [(77.0, 28.5), (77.01, 28.5)]])
        single = measure_geometry(shapely.LineString([(77.0, 28.4), (77.01, 28.4)]), WGS84)
        result = measure_geometry(geom, WGS84)
        assert result.length_m == pytest.approx(2 * single.length_m, rel=1e-3)

    def test_3d_coordinates_are_measured_in_2d(self):
        flat = measure_geometry(shapely.Polygon(SQUARE_LONLAT), WGS84)
        raised = measure_geometry(shapely.Polygon([(x, y, 250.0) for x, y in SQUARE_LONLAT]), WGS84)
        assert raised.area_m2 == pytest.approx(flat.area_m2)

    def test_large_polygon_matches_geodesic(self):
        # ~30° x 20° polygon over India: too wide for UTM, equal-area projection used.
        geom = shapely.Polygon([(65, 8), (95, 8), (95, 28), (65, 28)])
        result = measure_geometry(geom, WGS84)
        assert "+proj=laea" in result.projected_crs
        assert rel(result.area_m2, result.geodesic_area_m2) < 5e-3

    def test_invalid_polygon_is_repaired(self):
        bowtie = shapely.Polygon([(77.0, 28.4), (77.01, 28.41), (77.01, 28.4), (77.0, 28.41)])
        result = measure_geometry(bowtie, WGS84)
        assert result.status is MeasurementStatus.MEASURED
        assert result.area_m2 > 0
        assert "repaired" in result.message


class TestNonMeasurable:
    def test_point_not_applicable(self):
        assert measure_geometry(shapely.Point(77, 28), WGS84).status is MeasurementStatus.NOT_APPLICABLE

    def test_multipoint_not_applicable(self):
        geom = shapely.MultiPoint([(77, 28), (78, 29)])
        assert measure_geometry(geom, WGS84).status is MeasurementStatus.NOT_APPLICABLE

    def test_geometry_collection_unsupported(self):
        geom = shapely.GeometryCollection([shapely.Point(77, 28), shapely.LineString([(77, 28), (78, 28)])])
        result = measure_geometry(geom, WGS84)
        assert result.status is MeasurementStatus.UNSUPPORTED
        assert "GeometryCollection" in result.message

    def test_missing_geometry_skipped(self):
        assert measure_geometry(None, WGS84).status is MeasurementStatus.SKIPPED
        assert measure_geometry(shapely.Polygon(), WGS84).status is MeasurementStatus.SKIPPED

    def test_unknown_crs_skipped(self):
        result = measure_geometry(shapely.Polygon(SQUARE_UTM_1KM), None)
        assert result.status is MeasurementStatus.SKIPPED
        assert "CRS" in result.message
