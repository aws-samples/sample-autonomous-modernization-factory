"""Safe archive extraction and packaging for the modernization web app.

Archive_Extractor (Requirement 2): hardened extraction of untrusted ZIP archives
guarding against path traversal, zip bombs (uncompressed size), excessive entry
counts, slow/hostile archives (timeout), and symlink entries.

Archive_Packager (Requirements 6, 12.9): packages a transformed output directory
into a single ZIP, preserving relative structure, including extra files (the
change report), and never leaving a partial archive on failure.

Correctness properties (design):
- Property 1 (round-trip): safe_extract(package(dir)) reproduces dir exactly.
- Property 2 (confinement): every extracted path stays within the workspace root.
"""

from __future__ import annotations

import os
import stat
import time
import zipfile
from pathlib import Path
from typing import Iterable, Optional


class ArchiveError(Exception):
    """Base class for archive handling errors."""


class ExtractionError(ArchiveError):
    """Raised when extraction is rejected or fails (with cleanup)."""


class PackagingError(ArchiveError):
    """Raised when packaging fails (no partial archive left behind)."""


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Detect a symlink entry via the Unix mode bits in external_attr."""
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _resolve_within(root: Path, name: str) -> Path:
    """Resolve an archive entry name against root, rejecting path traversal.

    Raises ExtractionError if the resolved path escapes root (via '..',
    absolute paths, or drive-relative tricks).
    """
    root = root.resolve()
    # Normalize separators; reject absolute member paths outright.
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExtractionError(
            f"Path-traversal violation: entry '{name}' escapes the workspace"
        ) from exc
    return candidate


def _cleanup(dest: Path) -> None:
    """Remove partially written output. Best effort; never raises."""
    import shutil

    try:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
    except Exception:
        pass


def safe_extract(
    zip_path: os.PathLike | str,
    dest: os.PathLike | str,
    *,
    max_bytes: int,
    max_entries: int,
    timeout_s: int,
) -> Path:
    """Safely extract a ZIP archive into ``dest``.

    Enforces (Requirement 2):
      - path-traversal rejection (2.2)
      - cumulative uncompressed-size cap ``max_bytes`` (2.3)
      - entry-count cap ``max_entries`` (2.4)
      - overall ``timeout_s`` (2.5)
      - symlink entries excluded, continue with the rest (2.6)

    On any breach or error, partially written output is removed and an
    ExtractionError is raised. Returns the destination path on success.
    """
    zip_path = Path(zip_path)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()

    start = time.monotonic()
    written_bytes = 0
    written_entries = 0

    try:
        if not zipfile.is_zipfile(zip_path):
            raise ExtractionError("File is not a valid ZIP archive")

        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ExtractionError(
                    f"Entry-count limit exceeded: {len(infos)} > {max_entries}"
                )

            for info in infos:
                if time.monotonic() - start > timeout_s:
                    raise ExtractionError(
                        f"Extraction timeout exceeded ({timeout_s}s)"
                    )

                # Skip symlink entries entirely, but keep going (2.6).
                if _is_symlink_entry(info):
                    continue

                target = _resolve_within(root, info.filename)

                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                written_entries += 1
                if written_entries > max_entries:
                    raise ExtractionError(
                        f"Entry-count limit exceeded: > {max_entries}"
                    )

                target.parent.mkdir(parents=True, exist_ok=True)

                # Stream-copy, enforcing the size cap on ACTUAL bytes read so a
                # lying ZipInfo.file_size cannot smuggle a zip bomb through.
                with zf.open(info, "r") as src, open(target, "wb") as out:
                    while True:
                        if time.monotonic() - start > timeout_s:
                            raise ExtractionError(
                                f"Extraction timeout exceeded ({timeout_s}s)"
                            )
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        written_bytes += len(chunk)
                        if written_bytes > max_bytes:
                            raise ExtractionError(
                                f"Uncompressed-size limit exceeded (> {max_bytes} bytes)"
                            )
                        out.write(chunk)
    except ExtractionError:
        _cleanup(dest)
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _cleanup(dest)
        raise ExtractionError(f"Extraction failed: {exc}") from exc

    return dest


def package(
    src_dir: os.PathLike | str,
    out_zip: os.PathLike | str,
    extra_files: Optional[Iterable[os.PathLike | str]] = None,
) -> Path:
    """Package ``src_dir`` into a single ZIP at ``out_zip``.

    - Includes every regular file under ``src_dir`` preserving relative paths
      (Requirement 6.5).
    - Appends ``extra_files`` (e.g. the change report) at the archive root
      (Requirement 12.9).
    - Symlinks are skipped (consistent with extraction).
    - Writes to a temp file and atomically renames, so a failure never leaves a
      partial archive (Requirement 6.6). Returns the output path.
    """
    src_dir = Path(src_dir)
    out_zip = Path(out_zip)
    tmp = out_zip.with_name(out_zip.name + ".tmp")

    try:
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(src_dir.rglob("*")):
                if path.is_symlink():
                    continue
                if path.is_file():
                    arcname = path.relative_to(src_dir).as_posix()
                    zf.write(path, arcname)
            for extra in extra_files or []:
                extra_path = Path(extra)
                if extra_path.is_file():
                    zf.write(extra_path, extra_path.name)
        tmp.replace(out_zip)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise PackagingError(f"Packaging failed: {exc}") from exc

    return out_zip
