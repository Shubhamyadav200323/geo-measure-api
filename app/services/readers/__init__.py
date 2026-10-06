"""Format detection and dispatch.

Accepted uploads:
* ``.kml``                       -> KML reader
* ``.zip`` containing ``.shp``   -> Shapefile reader (all layers in the archive)
* ``.kmz`` / ``.zip`` with ``.kml`` -> KML reader (KMZ is a zipped KML)

Detection is by extension *and* content (ZIP magic number / XML root), so a
renamed file is rejected with a clear message instead of failing obscurely.
"""

from __future__ import annotations

from pathlib import Path

from app.core.exceptions import InvalidGeoFileError, UnsupportedFileTypeError
from app.db.models import FileFormat
from app.services.readers.archive import safe_extract
from app.services.readers.base import ReadResult
from app.services.readers.kml_reader import read_kml
from app.services.readers.shapefile_reader import read_shapefiles

ACCEPTED_EXTENSIONS = {".zip", ".kml", ".kmz"}
_ZIP_MAGIC = b"PK\x03\x04"


def validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or filename}'. Upload a .kml file or a .zip containing a Shapefile."
        )
    return ext


def read_geo_file(path: Path, filename: str, workdir: Path) -> ReadResult:
    """Parse an uploaded file. ``workdir`` is a scratch directory for extraction."""
    ext = validate_extension(filename)
    with open(path, "rb") as fh:
        head = fh.read(4)

    if ext == ".kml":
        if head == _ZIP_MAGIC:
            raise InvalidGeoFileError("File has a .kml extension but is a ZIP archive; rename it to .kmz.")
        return read_kml(path)

    if head != _ZIP_MAGIC:
        raise InvalidGeoFileError(f"File has a {ext} extension but is not a ZIP archive.")

    files = safe_extract(path, workdir)
    shps = [f for f in files if f.suffix.lower() == ".shp"]
    kmls = [f for f in files if f.suffix.lower() == ".kml"]

    if shps:
        return read_shapefiles(shps)
    if kmls:
        # KMZ convention: the main document is doc.kml, else the first .kml at the root.
        main = next((k for k in kmls if k.name.lower() == "doc.kml"), sorted(kmls, key=lambda p: len(p.parts))[0])
        result = read_kml(main, FileFormat.KMZ)
        if len(kmls) > 1:
            result.warnings.append(f"Archive contains {len(kmls)} KML files; only '{main.name}' was read.")
        return result

    raise InvalidGeoFileError("Archive contains no Shapefile (.shp) or KML (.kml) data.")
