"""Tests for src/upload.py - Upload_Handler (Task 6.2).

Uses moto to mock S3. Covers Requirements 1.1–1.7, 10.1.
Run with: pytest tests/test_upload.py -v
"""

import io
import sys
import zipfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.upload import UploadHandler, UploadValidationError

BUCKET = "mod-factory-artifacts-test"


def _bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    return s3


def _zip_bytes(entries=None):
    entries = entries or {"app.py": b"print('hi')\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


@mock_aws
def test_store_valid_zip_returns_id_and_stores():
    s3 = _bucket()
    h = UploadHandler(BUCKET, max_bytes=10 * 1024 * 1024)
    uid = h.store_upload("code.zip", _zip_bytes())
    assert uid and len(uid) == 32
    # object stored at the derived key
    assert h.upload_exists(uid)
    s3.head_object(Bucket=BUCKET, Key=f"uploads/{uid}/source.zip")


@mock_aws
def test_reject_missing_file():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=1024)
    with pytest.raises(UploadValidationError) as e:
        h.store_upload(None, None)
    assert e.value.code == "missing"


@mock_aws
def test_reject_non_zip_extension():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=1024)
    with pytest.raises(UploadValidationError) as e:
        h.store_upload("code.tar", io.BytesIO(b"data"))
    assert e.value.code == "not_zip"


@mock_aws
def test_reject_empty():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=1024)
    with pytest.raises(UploadValidationError) as e:
        h.store_upload("code.zip", io.BytesIO(b""))
    assert e.value.code == "empty"


@mock_aws
def test_reject_oversize():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=100)
    big = _zip_bytes({"big.bin": b"A" * 5000})
    with pytest.raises(UploadValidationError) as e:
        h.store_upload("code.zip", big)
    assert e.value.code == "too_large"


@mock_aws
def test_reject_corrupt_zip():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=10 * 1024)
    # .zip extension but not a real zip
    with pytest.raises(UploadValidationError) as e:
        h.store_upload("code.zip", io.BytesIO(b"not really a zip file"))
    assert e.value.code == "corrupt"


@mock_aws
def test_unique_ids_for_each_upload():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=10 * 1024 * 1024)
    a = h.store_upload("a.zip", _zip_bytes())
    b = h.store_upload("b.zip", _zip_bytes())
    assert a != b


@mock_aws
def test_upload_exists_false_for_unknown():
    _bucket()
    h = UploadHandler(BUCKET, max_bytes=1024)
    assert h.upload_exists("does-not-exist") is False
