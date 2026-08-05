"""Tests for src/run_manager.py - Run_Manager (Task 8.3, 8.4).

Uses moto for S3/DynamoDB and a fake CodeBuild client. Covers Requirements
4.1/4.4/4.5/4.7, 5.2–5.5, 11.10, 11.12 and design Property 4 (terminal-state
monotonicity).

Run with: pytest tests/test_run_manager.py -v
"""

import io
import json
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import AppStatus
from src.run_manager import (
    RunManager,
    TransformNotConfiguredError,
    UploadNotFoundError,
)
from src.store import RunStore
from src.upload import UploadHandler
from src.webconfig import Settings

ARTIFACTS = "mf-artifacts-test"
RESULTS = "mf-results-test"
TABLE = "mf-state-test"


class FakeCodeBuild:
    """Minimal stand-in for the CodeBuild client."""

    def __init__(self, status="IN_PROGRESS", fail_start=False):
        self.status = status
        self.fail_start = fail_start
        self.started = []

    def start_build(self, **kwargs):
        if self.fail_start:
            raise RuntimeError("StartBuild denied")
        self.started.append(kwargs)
        return {"build": {"id": "build-abc"}}

    def batch_get_builds(self, ids):
        return {"builds": [{"id": ids[0], "buildStatus": self.status}]}


def _setup(region="us-east-1"):
    s3 = boto3.client("s3", region_name=region)
    s3.create_bucket(Bucket=ARTIFACTS)
    s3.create_bucket(Bucket=RESULTS)
    ddb = boto3.resource("dynamodb", region_name=region)
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "app_id", "KeyType": "HASH"},
                   {"AttributeName": "run_id", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "app_id", "AttributeType": "S"},
                              {"AttributeName": "run_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return s3


def _settings(**over):
    base = dict(
        artifacts_bucket=ARTIFACTS, results_bucket=RESULTS, state_table=TABLE,
        aws_region="us-east-1", transformer_codebuild_project="transformer",
    )
    base.update(over)
    return Settings(**base).validate()


def _components(cb, s3):
    settings = _settings()
    store = RunStore(TABLE, resource=boto3.resource("dynamodb", region_name="us-east-1"))
    uploads = UploadHandler(ARTIFACTS, max_bytes=10 * 1024 * 1024, client=s3)
    return settings, store, uploads


def _upload_zip(s3, uploads):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", b"print('hi')\n")
    buf.seek(0)
    return uploads.store_upload("code.zip", buf)


def _put_result(s3, run_id, payload):
    s3.put_object(Bucket=RESULTS, Key=f"results/{run_id}/result.json",
                  Body=json.dumps(payload).encode())


@mock_aws
def test_create_run_starts_transforming():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="IN_PROGRESS")
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)

    rec = mgr.create_run(uid)
    assert rec.status == AppStatus.TRANSFORMING
    assert rec.build_id == "build-abc"
    assert cb.started  # a build was started


@mock_aws
def test_create_run_unknown_upload():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    cb = FakeCodeBuild()
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    with pytest.raises(UploadNotFoundError):
        mgr.create_run("missing-upload")


@mock_aws
def test_create_run_bad_runtime():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild()
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    with pytest.raises(ValueError):
        mgr.create_run(uid, "does-not-exist")  # unknown transformation id


@mock_aws
def test_create_run_not_configured():
    s3 = _setup()
    store = RunStore(TABLE, resource=boto3.resource("dynamodb", region_name="us-east-1"))
    uploads = UploadHandler(ARTIFACTS, max_bytes=10 * 1024 * 1024, client=s3)
    uid = _upload_zip(s3, uploads)
    settings = _settings(transformer_codebuild_project="")  # not configured
    cb = FakeCodeBuild()
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    with pytest.raises(TransformNotConfiguredError):
        mgr.create_run(uid)


@mock_aws
def test_create_run_start_failure_marks_failed():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(fail_start=True)
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)
    assert rec.status == AppStatus.FAILED
    assert store.get_run(rec.run_id).status == AppStatus.FAILED


@mock_aws
def test_refresh_completed():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="IN_PROGRESS")
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)

    cb.status = "SUCCEEDED"
    _put_result(s3, rec.run_id, {
        "status": "COMPLETED",
        "artifact_key": f"results/{rec.run_id}/modernized.zip",
        "report_key": f"results/{rec.run_id}/change_report.json",
    })
    updated = mgr.refresh_status(rec.run_id)
    assert updated.status == AppStatus.COMPLETED
    assert updated.artifact_key.endswith("modernized.zip")


@mock_aws
def test_refresh_escalated():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="SUCCEEDED")
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)
    _put_result(s3, rec.run_id, {"status": "ESCALATED", "escalation_reason": "needs human"})
    updated = mgr.refresh_status(rec.run_id)
    assert updated.status == AppStatus.ESCALATED
    assert "human" in updated.escalation_reason


@mock_aws
def test_refresh_build_failed():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="FAILED")
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)
    updated = mgr.refresh_status(rec.run_id)
    assert updated.status == AppStatus.FAILED


@mock_aws
def test_refresh_unknown_run_returns_none():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    cb = FakeCodeBuild()
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    assert mgr.refresh_status("run-ghost") is None


@mock_aws
def test_timeout_marks_failed():
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="IN_PROGRESS")
    # zero-minute timeout is clamped to 1; force created_at into the past instead
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)
    # backdate created_at beyond the timeout
    store.update_status(rec.run_id, AppStatus.TRANSFORMING,
                        created_at=(datetime.now(timezone.utc) - timedelta(minutes=999)).isoformat())
    updated = mgr.refresh_status(rec.run_id)
    assert updated.status == AppStatus.FAILED
    assert "timed out" in updated.error_message


@mock_aws
def test_terminal_state_monotonicity_property():
    """Property 4: a terminal run never reverts to a non-terminal status."""
    s3 = _setup()
    settings, store, uploads = _components(None, s3)
    uid = _upload_zip(s3, uploads)
    cb = FakeCodeBuild(status="SUCCEEDED")
    mgr = RunManager(settings, store, uploads, codebuild_client=cb, s3_client=s3)
    rec = mgr.create_run(uid)
    _put_result(s3, rec.run_id, {"status": "COMPLETED",
                                 "artifact_key": "k", "report_key": "r"})
    assert mgr.refresh_status(rec.run_id).status == AppStatus.COMPLETED

    # Even if CodeBuild now reports IN_PROGRESS, status stays COMPLETED.
    cb.status = "IN_PROGRESS"
    assert mgr.refresh_status(rec.run_id).status == AppStatus.COMPLETED
