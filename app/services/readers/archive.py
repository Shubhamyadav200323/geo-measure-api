"""Safe extraction of uploaded ZIP / KMZ archives.

Guards against:
* zip-slip (entries such as ``../../etc/passwd`` or absolute paths),
* zip bombs (limits on total uncompressed size and number of entries),
* symlinks inside the archive.
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.core.config import get_settings
from app.core.exceptions import FileTooLargeError, InvalidGeoFileError

# Noise produced by macOS Finder and other tools
_IGNORED_PARTS = {"__MACOSX", ".DS_Store"}


def safe_extract(archive_path: Path, dest: Path) -> list[Path]:
    settings = get_settings()
    try:
        zf = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise InvalidGeoFileError("Uploaded archive is not a valid ZIP file.") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > settings.max_archive_entries:
            raise InvalidGeoFileError(
                f"Archive contains {len(infos)} files; the limit is {settings.max_archive_entries}."
            )
        total = sum(i.file_size for i in infos)
        if total > settings.max_uncompressed_bytes:
            raise FileTooLargeError(
                f"Archive expands to {total / 1e6:.1f} MB; the limit is {settings.max_uncompressed_size_mb} MB."
            )

        dest = dest.resolve()
        extracted: list[Path] = []
        written = 0  # cumulative across all entries
        for info in infos:
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if any(part in _IGNORED_PARTS or part.startswith("._") for part in name.parts):
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise InvalidGeoFileError(f"Archive entry '{info.filename}' is a symlink, which is not allowed.")
            target = (dest / name).resolve()
            if name.is_absolute() or not target.is_relative_to(dest):
                raise InvalidGeoFileError(f"Archive entry '{info.filename}' escapes the extraction directory.")

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                # Count real bytes: the header's declared size can lie.
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > settings.max_uncompressed_bytes:
                        raise FileTooLargeError("Archive contents exceed the uncompressed size limit.")
                    out.write(chunk)
            extracted.append(target)
        return extracted
