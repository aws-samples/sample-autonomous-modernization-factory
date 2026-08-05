"""Upload_Handler - validate and store an uploaded codebase archive.

Requirements 1 (upload validation) and 10.1/10.2 (per-upload storage isolation).
Stores the archive to s3://<artifacts>/uploads/<upload_id>/source.zip.

The API layer adapts a FastAPI UploadFile to store_upload(filename, fileobj).
"""

from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from typing import BinaryIO, Optional

import boto3
from botocore.exceptions import ClientError

_CHUNK = 1024 * 1024


class UploadValidationError(Exception):
    """Raised when an upload is rejected. ``code`` is a stable machine token."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.message = message


class UploadHandler:
    def __init__(self, bucket: str, region: str = "us-east-1", *, max_bytes: int,
                 client=None):
        self._s3 = client or boto3.client("s3", region_name=region)
        self._bucket = bucket
        self._max_bytes = max_bytes

    def source_key(self, upload_id: str) -> str:
        return f"uploads/{upload_id}/source.zip"

    def store_upload(self, filename: Optional[str], fileobj: Optional[BinaryIO]) -> str:
        """Validate and store an uploaded ZIP; return a unique upload_id.

        Rejections (no partial data retained): missing file, non-zip extension,
        oversize, empty, corrupt/unreadable zip (Req 1.3–1.7).
        """
        if not filename or fileobj is None:
            raise UploadValidationError("a file is required", "missing")
        if not filename.lower().endswith(".zip"):
            raise UploadValidationError("only ZIP archives are accepted", "not_zip")

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            total = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = fileobj.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise UploadValidationError(
                            f"archive exceeds the maximum accepted size of {self._max_bytes} bytes",
                            "too_large",
                        )
                    out.write(chunk)

            if total == 0:
                raise UploadValidationError("the archive is empty", "empty")

            if not zipfile.is_zipfile(tmp_path):
                raise UploadValidationError(
                    "the archive is unreadable or corrupted", "corrupt"
                )
            with zipfile.ZipFile(tmp_path) as zf:
                if zf.testzip() is not None:
                    raise UploadValidationError(
                        "the archive is unreadable or corrupted", "corrupt"
                    )

            upload_id = uuid.uuid4().hex
            key = self.source_key(upload_id)
            # uuid4 makes the prefix unique; guard defensively against collision.
            if self._object_exists(key):
                raise UploadValidationError(
                    "could not assign a unique workspace for the upload", "workspace"
                )
            self._s3.upload_file(tmp_path, self._bucket, key)
            return upload_id
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def upload_exists(self, upload_id: str) -> bool:
        return self._object_exists(self.source_key(upload_id))

    def list_entries(self, upload_id: str) -> list[str]:
        """Return the file names inside a stored upload archive.

        Reads only the ZIP central directory (no extraction, no code execution),
        so it is safe to call from the API tier. Returns [] if unavailable.
        """
        import io

        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self.source_key(upload_id))
            data = obj["Body"].read()
        except ClientError:
            return []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return zf.namelist()
        except (zipfile.BadZipFile, OSError):
            return []

    def _object_exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
