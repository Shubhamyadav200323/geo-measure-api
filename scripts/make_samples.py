"""Generate the files in ``sample_data/``.  Run: ``python -m scripts.make_samples``"""

from pathlib import Path

from pyproj import Transformer

from tests.factories import SAMPLE_KML, SQUARE_LONLAT, SQUARE_UTM_1KM, build_shapefile_zip

OUT = Path(__file__).resolve().parent.parent / "sample_data"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "survey.kml").write_text(SAMPLE_KML)

    # Polygons in geographic coordinates (EPSG:4326)
    parcels = [
        ({"type": "Polygon", "coordinates": [SQUARE_LONLAT]}, {"name": "Parcel A", "owner": "Sharma"}),
        (
            {"type": "Polygon", "coordinates": [[[77.02, 28.40], [77.03, 28.40], [77.03, 28.41], [77.02, 28.41],
                                                 [77.02, 28.40]]]},
            {"name": "Parcel B", "owner": "Verma"},
        ),
    ]
    (OUT / "parcels_wgs84.zip").write_bytes(build_shapefile_zip(parcels, name="parcels", epsg=4326))

    # The same region in a projected CRS (UTM 43N): a 1 km x 1 km square
    utm = [({"type": "Polygon", "coordinates": [SQUARE_UTM_1KM]}, {"name": "1 km square"})]
    (OUT / "square_utm43n.zip").write_bytes(build_shapefile_zip(utm, name="square", epsg=32643))

    # Lines in Web Mercator, to show that the source CRS units are not used directly
    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    road = [list(to_merc.transform(x, y)) for x, y in [(77.0, 28.395), (77.01, 28.395), (77.03, 28.395)]]
    roads = [({"type": "LineString", "coordinates": road}, {"name": "Access road"})]
    (OUT / "roads_webmercator.zip").write_bytes(build_shapefile_zip(roads, name="roads", epsg=3857))

    print(f"Wrote samples to {OUT}")


if __name__ == "__main__":
    main()
