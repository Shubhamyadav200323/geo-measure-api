# Geospatial File Measurement API

A FastAPI service that accepts a **KML file** or a **zipped Shapefile**, extracts every feature
(ID, geometry type, geometry, CRS, attributes) and computes **areas for polygons** and
**lengths for lines** in a locally appropriate *projected* coordinate system — never in degrees.

```
POST /api/files/                     upload + process
GET  /api/files/{id}/                file information
GET  /api/files/{id}/measurements/   per-feature area / length + totals
GET  /api/files/{id}/features/       geometry, CRS and properties per feature
```

---

## Contents
- [Setup](#setup)
- [API](#api)
- [Architecture](#architecture)
- [Design decisions](#design-decisions)
- [Testing](#testing)
- [Learnings](#learnings)
- [Future scope](#future-scope)

---

## Setup

**Requirements:** Python 3.11+. No system GDAL/GEOS/PROJ install is needed — every
geospatial dependency ships as a binary wheel.

```bash
git clone https://github.com/<your-username>/geo-measure-api.git
cd geo-measure-api

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Interactive docs (Swagger UI): <http://localhost:8000/docs>
- OpenAPI schema: <http://localhost:8000/openapi.json>

The SQLite database and stored uploads are created under `./data/` on first start.

**With Docker**

```bash
docker build -t geo-measure-api .
docker run -p 8000:8000 geo-measure-api
```

**Configuration** — all optional, via environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/geo_measure.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `GEO_UPLOAD_DIR` | `./data/uploads` | Where original uploads are kept |
| `GEO_MAX_UPLOAD_SIZE_MB` | `50` | Max upload size |
| `GEO_MAX_UNCOMPRESSED_SIZE_MB` | `500` | Zip-bomb guard: max extracted size |
| `GEO_MAX_ARCHIVE_ENTRIES` | `1000` | Zip-bomb guard: max files in an archive |
| `GEO_UTM_MAX_LON_SPAN_DEG` | `6.0` | Wider features use an azimuthal projection instead of UTM |

**Sample data** in `sample_data/` (regenerate with `python -m scripts.make_samples`):

| File | Contents |
|---|---|
| `survey.kml` | Polygons (one with a hole), a line, a point, a `gx:Track`, a mixed `MultiGeometry`, a multipolygon and a placemark with no geometry |
| `parcels_wgs84.zip` | Shapefile, polygons in EPSG:4326 |
| `square_utm43n.zip` | Shapefile, an exact 1 km × 1 km square in EPSG:32643 (UTM 43N) |
| `roads_webmercator.zip` | Shapefile, a line in EPSG:3857 (Web Mercator) |

---

## API

All error responses share one shape: `{"detail": "<message>", "code": "<machine_code>"}`.

### `POST /api/files/` — upload and process

Multipart form with a single field `file`. Accepted:

- `.kml`
- `.zip` containing a Shapefile (`.shp` + `.shx` + `.dbf`, optional `.prj` / `.cpg`; sub-folders and multiple layers are fine)
- `.kmz` (zipped KML) — bonus

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```

`201 Created`

```json
{
  "id": "1e2d899c416a49d683a0d641c6049672",
  "filename": "survey.kml",
  "file_format": "KML",
  "size_bytes": 2715,
  "feature_count": 8,
  "crs": "EPSG:4326",
  "crs_name": "WGS 84",
  "status": "COMPLETED",
  "error": null,
  "warnings": [],
  "layers": [
    { "name": "survey", "feature_count": 8, "crs": "EPSG:4326", "crs_assumed": false }
  ],
  "geometry_types": {
    "Polygon": 2, "LineString": 2, "MultiPolygon": 1, "Point": 1, "GeometryCollection": 1, "None": 1
  },
  "created_at": "2026-10-06T15:09:44.800945Z",
  "processed_at": "2026-10-06T15:09:44.821905Z"
}
```

| Status | When |
|---|---|
| `201` | Processed (`status: COMPLETED`) |
| `413` | Upload or extracted archive too large |
| `415` | Extension not `.kml` / `.zip` / `.kmz` |
| `422` | File stored but unreadable — body is the file record with `status: FAILED` and `error`, so it can still be fetched later by `id` |

```bash
$ curl -F "file=@broken.kml" http://localhost:8000/api/files/
# 422
{"id": "893e…", "filename": "broken.kml", "status": "FAILED",
 "error": "File is not well-formed XML: no element found: line 1, column 16", ...}
```

### `GET /api/files/{id}/` — file information

```bash
curl http://localhost:8000/api/files/1e2d899c416a49d683a0d641c6049672/
```

Same body as the upload response. `404` if the id is unknown.

### `GET /api/files/{id}/measurements/` — measurements

Query parameters: `limit` (1–1000, default 100), `offset`, `geometry_type` (e.g. `Polygon`),
`status` (`MEASURED`, `NOT_APPLICABLE`, `UNSUPPORTED`, `SKIPPED`, `ERROR`).
The `summary` always covers the whole file, regardless of filters and pagination.

```bash
curl "http://localhost:8000/api/files/1e2d899c416a49d683a0d641c6049672/measurements/?limit=4"
```

```json
{
  "file_id": "1e2d899c416a49d683a0d641c6049672",
  "units": { "area": "square_metre", "length": "metre" },
  "summary": {
    "total_features": 8,
    "by_status": { "MEASURED": 5, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1, "SKIPPED": 1, "ERROR": 0 },
    "total_area_m2": 2672027.75,
    "total_length_m": 3920.39
  },
  "count": 8,
  "limit": 4,
  "offset": 0,
  "results": [
    {
      "feature_id": 0,
      "source_id": "parcel-1",
      "layer": null,
      "geometry_type": "Polygon",
      "status": "MEASURED",
      "area_m2": 1086167.92,
      "perimeter_m": 4176.67,
      "length_m": null,
      "projected_crs": "EPSG:32643",
      "geodesic_area_m2": 1086002.51,
      "geodesic_length_m": null,
      "message": null
    },
    {
      "feature_id": 2,
      "source_id": "road-1",
      "geometry_type": "LineString",
      "status": "MEASURED",
      "length_m": 2940.26,
      "projected_crs": "EPSG:32643",
      "geodesic_length_m": 2940.03,
      "...": "..."
    },
    {
      "feature_id": 3,
      "source_id": "well-1",
      "geometry_type": "Point",
      "status": "NOT_APPLICABLE",
      "message": "Points have no area or length.",
      "...": "..."
    },
    {
      "feature_id": 4,
      "source_id": "mixed-1",
      "geometry_type": "GeometryCollection",
      "status": "UNSUPPORTED",
      "message": "Measurement is not supported for geometry type 'GeometryCollection'.",
      "...": "..."
    }
  ]
}
```

(Abbreviated with `"..."`; every result has all fields.) Returns `409 Conflict` if the file
did not process successfully.

**Measurement statuses**

| Status | Meaning |
|---|---|
| `MEASURED` | `Polygon`/`MultiPolygon` → `area_m2` + `perimeter_m`; `LineString`/`MultiLineString` → `length_m` |
| `NOT_APPLICABLE` | `Point`/`MultiPoint` — nothing to measure |
| `UNSUPPORTED` | e.g. a mixed `GeometryCollection`, a KML `Model` |
| `SKIPPED` | No geometry, or the CRS is unknown (see [CRS handling](#crs-handling)) |
| `ERROR` | That feature's geometry was malformed; other features are unaffected |

`geodesic_area_m2` / `geodesic_length_m` are reference values computed directly on the
WGS84 ellipsoid (Karney's algorithm), returned alongside the projected result so the
accuracy of the projection is visible.

### `GET /api/files/{id}/features/` — features

Geometry (GeoJSON, coordinates in the feature's `crs`), CRS and attributes.
Same `limit` / `offset` / `geometry_type` parameters.

```json
{
  "count": 8, "limit": 1, "offset": 0,
  "results": [
    {
      "feature_id": 0,
      "source_id": "parcel-1",
      "layer": null,
      "geometry_type": "Polygon",
      "geometry": { "type": "Polygon",
                    "coordinates": [[[77.0, 28.4], [77.01, 28.4], [77.01, 28.41], [77.0, 28.41], [77.0, 28.4]]] },
      "crs": "EPSG:4326",
      "properties": { "name": "Parcel A", "owner": "Sharma", "landuse": "agriculture" }
    }
  ]
}
```

### Other endpoints

- `GET /api/files/` — paginated list of uploads (newest first)
- `DELETE /api/files/{id}/` — removes the record, its features and the stored upload (`204`)
- `GET /health` — liveness probe

---

## Architecture

### Application structure

```
app/
├── main.py                  # App factory, exception → HTTP mapping, lifespan (DB init)
├── schemas.py               # Pydantic request/response contract
├── api/files.py             # Thin HTTP layer: routing, pagination, status codes
├── core/
│   ├── config.py            # Settings from env vars (pydantic-settings)
│   └── exceptions.py        # Domain exceptions, each carrying its HTTP status + code
├── db/
│   ├── session.py           # Engine / session / Base
│   └── models.py            # UploadedFile 1──* Feature
└── services/
    ├── processor.py         # Orchestration: store → parse → measure → persist
    ├── measurement.py       # Per-feature area/length
    ├── crs.py               # CRS parsing, transformer cache, projected-CRS selection
    └── readers/
        ├── __init__.py      # Format detection + dispatch
        ├── archive.py       # Hardened ZIP extraction
        ├── base.py          # RawFeature / ReadResult data classes
        ├── kml_reader.py
        └── shapefile_reader.py
tests/                       # 61 tests: unit (measurement, readers) + API integration
```

Dependencies point one way: `api → services → (readers, measurement → crs)`. Readers only
parse; measurement only measures; neither knows about HTTP or the database. That keeps each
piece unit-testable and makes adding a format (GeoJSON, GeoPackage) a matter of writing one
reader that returns `RawFeature`s.

### File-processing flow

```
POST /api/files/
   │
   ▼
store_upload ── validate extension, sanitise filename, stream to disk in 1 MB chunks
   │            (size limit enforced while streaming) → UploadedFile(status=PENDING)
   ▼
process_file ── status=PROCESSING
   │
   ├── read_geo_file: sniff content (ZIP magic / XML root) — not just the extension
   │     ├── .kml         → kml_reader
   │     └── .zip / .kmz  → safe_extract → .shp found? → shapefile_reader (one layer per .shp)
   │                                      → .kml found? → kml_reader (KMZ)
   │
   ├── for each RawFeature: GeoJSON → Shapely → measure_geometry (isolated per feature)
   │
   └── persist Feature rows, file CRS, layer info, warnings → status=COMPLETED
         (any GeoServiceError → status=FAILED + error message; file record kept)
```

**KML:** walks every `Placemark` at any depth (`Document`/`Folder` nesting), namespace-agnostic
(KML 2.2, 2.1, or none). Supports `Point`, `LineString`, `LinearRing`, `Polygon` with holes,
nested `MultiGeometry` (homogeneous children → `MultiPolygon` etc.; mixed →
`GeometryCollection`), and `gx:Track`. Attributes come from `name`, `description`,
`ExtendedData/Data` and `SchemaData/SimpleData`; the Placemark `id` becomes `source_id`.

**Shapefile:** each `.shp` in the archive (any folder) is a layer; `.shx` and `.dbf` are
required, `.prj` gives the CRS, `.cpg` the attribute encoding (default UTF-8, invalid bytes
replaced rather than failing). Null shapes are kept as features with no geometry. Dates,
decimals and bytes in the DBF are coerced to JSON-safe values.

### Measurement calculation flow

For each feature (`app/services/measurement.py`):

1. **Classify** — points → `NOT_APPLICABLE`; types other than (multi)polygon/line → `UNSUPPORTED`;
   empty geometry or unknown CRS → `SKIPPED`.
2. **Normalise** — drop Z (measurements are 2D/planimetric); repair invalid polygons
   (self-intersections, bow-ties) with `shapely.make_valid`, noting it in `message`.
3. **Densify** long edges in the *source* CRS (max 0.01° / 1000 units per segment).
   Only vertices are reprojected, so without this an edge that is straight in the source
   CRS becomes a different chord after projection. On a 30° × 20° polygon this was a
   **2.3 % error before densifying and 0.0000004 % after**.
4. **Transform to WGS84** lon/lat (skipped if already EPSG:4326).
5. **Choose a projected CRS** for this feature (below) and transform into it.
6. **Measure** with Shapely in metres: `area` + boundary length for polygons, `length` for lines.
7. **Geodesic reference** — compute the same quantity on the ellipsoid with `pyproj.Geod`
   (polygons are oriented first so holes are subtracted).

A failure in any step affects only that feature (`status: ERROR`), never the file.

### CRS handling

**Identifying the source CRS**

| Input | CRS |
|---|---|
| KML / KMZ | Always EPSG:4326 (mandated by the OGC KML 2.2 spec) |
| Shapefile with `.prj` | Parsed with pyproj (ESRI and OGC WKT); exposed as `EPSG:xxxx` when it matches an EPSG code |
| Shapefile without `.prj`, coordinates within ±180/±90 | **Assumed** EPSG:4326, with `crs_assumed: true` and a warning |
| Shapefile without `.prj`, other coordinates | Unknown → features are returned but measurements are `SKIPPED` with an explanation. Guessing a projected CRS would produce confidently wrong numbers. |

When a ZIP contains layers in different CRSs, the file's `crs` is `"MIXED"` and each layer's
CRS is listed in `layers`.

**Choosing the projected CRS (per feature)**

| Feature | Projected CRS | Why |
|---|---|---|
| Longitude span ≤ 6° and \|lat\| ≤ 84° (almost all real survey data) | **UTM zone of the feature's centroid** (EPSG:326xx / 327xx) | Conformal, ≤ ~0.04 % scale error inside a zone, a standard EPSG code that users and other GIS tools recognise |
| Polygon too wide for one zone, or polar | **Lambert Azimuthal Equal-Area** centred on the feature | Equal-area by construction: area is exact regardless of size |
| Line too wide for one zone, or polar | **Azimuthal Equidistant** centred on the feature | Preserves distances from the centre; least-bad planar choice for long lines |

The CRS used is returned per feature as `projected_crs` (an EPSG code, or the PROJ string for
the custom azimuthal projections).

**The source CRS is never used for measuring, even if it is already projected.** Projected
inputs may use non-metric units (US survey feet) or be badly distorted — Web Mercator
(EPSG:3857) inflates lengths by 1/cos(lat), so the sample road in `roads_webmercator.zip`
would measure ~3,340 m in its native units instead of the correct 2,940 m. Everything is
routed source → WGS84 → local metric CRS, and a test asserts the same square gives the same
area whether it arrives in UTM, lon/lat or Web Mercator.

Transformers are built with `always_xy=True` (avoiding EPSG:4326's lat/lon axis-order trap)
and cached with `lru_cache`, since building one costs a PROJ database lookup and a file
typically reuses one or two CRS pairs across thousands of features.

---

## Design decisions

| Decision | Alternatives considered | Reasoning |
|---|---|---|
| **FastAPI** | Django + DRF | The service is a small, API-only surface. FastAPI gives typed request/response models, automatic OpenAPI docs and validation with very little code. Django's admin/ORM/auth would be mostly unused here. |
| **pyshp + own KML parser + Shapely + pyproj** | GeoPandas / Fiona / GDAL (`ogr`) | GDAL-based stacks read everything but drag in a large native dependency that is the #1 source of "doesn't install on my machine" problems. All chosen libraries are pip wheels — `pip install` just works on macOS/Windows/Linux and the Docker image needs no system packages. The trade-off is fewer formats; the reader interface makes adding GDAL later a contained change. |
| **Per-feature UTM, azimuthal fallback** | One CRS per file; Web Mercator; geodesic-only | A per-file CRS breaks for files spanning zones. Web Mercator is not suitable for measurement at all. Pure geodesic computation is the most accurate, but the requirement was to project, and UTM gives results that match what a surveyor sees in QGIS/ArcGIS. The geodesic values are still returned as a cross-check. |
| **Compute at upload, store typed columns** | Compute on every `GET` | Files are immutable once uploaded, so measuring once is cheapest. Typed `area_m2`/`length_m` columns (rather than a JSON blob) let totals be computed with SQL `SUM` and filtered by status/type. |
| **Synchronous processing with a status lifecycle** | Background tasks / Celery from day one | Synchronous keeps the API simple and deterministic for this scope (the upload response already contains the result). The `PENDING → PROCESSING → COMPLETED/FAILED` lifecycle and a framework-independent `process_file()` mean moving to a worker queue changes only the route handler. |
| **Failed parses are persisted (422 + record)** | Plain 4xx with no record | Rejecting the request outright loses the audit trail. Validation errors that need no file (wrong type, too big) are plain 4xx; content errors keep a `FAILED` record whose `error` explains why. |
| **Graceful per-feature status** | Fail the whole file on a bad feature | One malformed polygon in 10,000 should not discard 9,999 good measurements. Each feature reports its own status and message. |
| **Unknown CRS → SKIPPED, no guessing** | Assume metres, assume UTM | A silently wrong area is worse than no area. Lon/lat-looking data without a `.prj` is assumed WGS84 (overwhelmingly the real-world case) but flagged with `crs_assumed`. |
| **SQLite via SQLAlchemy** | PostgreSQL/PostGIS | Zero-setup for reviewers; switching is one env var (`GEO_DATABASE_URL`). PostGIS would add spatial queries but isn't needed for measurement, which happens in Python. |
| **Security hardening** | — | Uploads are untrusted: `defusedxml` blocks XXE/billion-laughs, ZIP extraction rejects path traversal and symlinks and counts real decompressed bytes (headers can lie), upload size is enforced while streaming, client filenames are reduced to a basename. |

---

## Testing

```bash
pytest          # 61 tests, ~1 s
ruff check .
```

Highlights:

- **Known answers:** an exact 1 km × 1 km UTM square measures 1,000,000 m²; one degree of the
  equator matches the WGS84 value of 111,319.49 m.
- **CRS independence:** the same square in UTM, lon/lat and Web Mercator gives the same area.
- **Projection accuracy:** projected results agree with ellipsoidal (geodesic) values within
  0.05 % for UTM-sized features and within 0.5 % for a 30° × 20° polygon (actual: ~4×10⁻⁹).
- **Robustness:** holes, multipart, 3D coordinates, invalid polygons, null shapes, missing
  `.prj`, missing `.dbf`, nested folders, multiple layers, KMZ, malformed XML, XML entity
  attacks, zip-slip, renamed files.

CI runs lint + tests on Python 3.11–3.13 (`.github/workflows/ci.yml`).

---

## Learnings

- **Reprojecting vertices isn't reprojecting a geometry.** Edges stay straight in whatever CRS
  you're in, so long edges must be densified before transformation. My first version passed
  every small-parcel test and was 2.3 % off on a country-sized polygon; the geodesic
  cross-check is what exposed it.
- **Geodesic polygon area depends on ring orientation.** pyproj returns a signed area, so a
  hole wound the same way as its shell is *added*. KML doesn't mandate winding order, so
  polygons must be oriented first — another bug the cross-check caught.
- **"Projected" doesn't mean "measurable".** Web Mercator data is projected and in metres, yet
  its areas are wrong by ~30 % in north India. The only safe rule is to always choose the
  measuring CRS yourself.
- **Axis order bites.** EPSG:4326 is officially lat/lon; GeoJSON, KML and Shapefile are lon/lat.
  `always_xy=True` everywhere avoids silently swapped coordinates.
- **Untrusted geodata is an attack surface:** XML entity expansion and zip bombs are as
  relevant to a GIS upload endpoint as to any other.

## Future scope

- **Asynchronous processing** — return `202 Accepted` and process in a worker (Celery/RQ/arq)
  for large files; the status lifecycle is already in place. Add a webhook or SSE for completion.
- **Streaming ingestion** — read and insert features in batches to bound memory for files with
  millions of features.
- **More formats** — GeoJSON, GeoPackage, GML, GPX, CSV with WKT, via an optional GDAL-backed
  reader behind the same interface.
- **Selectable measurement method** — e.g. `?method=geodesic|projected` and `?units=ha|acre|km`,
  or a user-supplied target CRS.
- **Antimeridian-aware handling** — split features that cross ±180° (currently flagged with a
  warning) and use UTM zone exceptions for Norway/Svalbard.
- **PostGIS + Alembic** — spatial indexing, bounding-box queries over features, and proper
  schema migrations.
- **Exports** — download features + measurements as GeoJSON/CSV.
- **Operations** — authentication and per-user file ownership, object storage (S3) for
  uploads, rate limiting, structured logging and metrics, file retention policy.
