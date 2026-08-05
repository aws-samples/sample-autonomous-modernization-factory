"""Opt-in end-to-end integration test (Task 14.2).

Runs one real transformation against a small sample repo using deployed AWS
infrastructure. Skipped unless MF_RUN_INTEGRATION=1 and the required settings
are present, so CI without AWS credentials still passes.

Enable with:
    MF_RUN_INTEGRATION=1 \
    MF_ARTIFACTS_BUCKET=... MF_RESULTS_BUCKET=... MF_STATE_TABLE=... \
    MF_TRANSFORMER_PROJECT=... AWS_REGION=us-east-1 \
    pytest tests/test_integration.py -v

Covers Requirements 4.2, 6.2, 11.6, 12.1 against live services.
"""

import io
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

RUN_IT = os.environ.get("MF_RUN_INTEGRATION") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_IT, reason="integration test disabled (set MF_RUN_INTEGRATION=1)"
)


def _sample_zip() -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", b"print 'legacy'\n")  # py2 pattern
        zf.writestr("requirements.txt", b"\n")
    buf.seek(0)
    return buf


def test_end_to_end_transformation():
    from src.webconfig import load_settings
    from src.store import RunStore
    from src.upload import UploadHandler
    from src.run_manager import RunManager
    from src.models import AppStatus

    settings = load_settings()
    missing = [k for k, v in {
        "artifacts_bucket": settings.artifacts_bucket,
        "results_bucket": settings.results_bucket,
        "state_table": settings.state_table,
        "transformer_codebuild_project": settings.transformer_codebuild_project,
    }.items() if not v]
    if missing:
        pytest.skip(f"integration settings missing: {missing}")

    store = RunStore(settings.state_table, settings.aws_region)
    uploads = UploadHandler(settings.artifacts_bucket, settings.aws_region,
                            max_bytes=settings.max_upload_bytes)
    mgr = RunManager(settings, store, uploads)

    upload_id = uploads.store_upload("sample.zip", _sample_zip())
    record = mgr.create_run(upload_id, settings.default_target_runtime)
    assert record.run_id  # Req 4.2 - run id returned promptly

    # Poll until terminal (bounded).
    deadline = time.time() + settings.transformation_timeout_min * 60 + 120
    status = record.status
    while time.time() < deadline:
        cur = mgr.refresh_status(record.run_id)
        status = cur.status
        if status in (AppStatus.COMPLETED, AppStatus.ESCALATED, AppStatus.FAILED):
            break
        time.sleep(15)

    assert status == AppStatus.COMPLETED, f"run ended as {status}"
    final = mgr.get_run(record.run_id)
    assert final.artifact_key  # Req 6.2 - downloadable artifact
    assert final.report_key    # Req 12.1 - change report produced
