"""Tests for src/web_models.py (Task 2.2).

Covers Requirements 3.3, 3.4, 3.5, 3.6, 9.1, 9.7.
Run with: pytest tests/test_web_models.py -v
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import AppStatus, UpgradeType
from src.web_models import (
    ChangeReport,
    RunRecord,
    RunStatusResponse,
    StartRunRequest,
    validate_target_runtime,
)


# --- Target_Runtime format (Req 3.6) ---
@pytest.mark.parametrize("good", ["python3.12", "python3.9", "python2.7", "python10.0"])
def test_valid_runtime_formats(good):
    assert validate_target_runtime(good) == good


@pytest.mark.parametrize("bad", ["python3", "py3.12", "3.12", "python3.x", "", "cpython3.12"])
def test_invalid_runtime_formats_rejected(bad):
    with pytest.raises(ValueError):
        validate_target_runtime(bad)


# --- StartRunRequest defaulting + validation (Req 3.3, 3.4, 3.5, 3.6) ---
def test_start_request_defaults():
    req = StartRunRequest(upload_id="u1")
    assert req.transformation_id == "python-version-upgrade"
    assert req.target is None


def test_start_request_rejects_unknown_transformation():
    with pytest.raises(ValidationError):
        StartRunRequest(upload_id="u1", transformation_id="does-not-exist")


def test_start_request_accepts_known_transformation_and_target():
    req = StartRunRequest(upload_id="u1", transformation_id="java-version-upgrade", target="Java 17")
    assert req.transformation_id == "java-version-upgrade"
    assert req.target == "Java 17"


def test_start_request_target_runtime_alias():
    req = StartRunRequest(upload_id="u1", target_runtime="python3.12")
    assert req.target == "python3.12"


# --- RunRecord round-trip (Req 9.1) ---
def test_run_record_item_roundtrip():
    rec = RunRecord(run_id="r1", app_id="u1", upload_id="u1", source_key="uploads/u1/source.zip")
    item = rec.to_item()
    restored = RunRecord.from_item(item)
    assert restored.run_id == "r1"
    assert restored.status == AppStatus.QUEUED
    assert restored.target_runtime == "python3.12"


def test_reason_truncation():
    rec = RunRecord(run_id="r", app_id="u", upload_id="u", error_message="x" * 5000)
    assert len(rec.error_message) == 1000


# --- TTL on terminal (Req 9.7) ---
def test_terminal_ttl_set():
    rec = RunRecord(run_id="r", app_id="u", upload_id="u", status=AppStatus.COMPLETED)
    assert rec.is_terminal
    with_ttl = rec.with_terminal_ttl()
    assert with_ttl.ttl is not None and with_ttl.ttl > 0


def test_non_terminal_not_flagged():
    rec = RunRecord(run_id="r", app_id="u", upload_id="u", status=AppStatus.TRANSFORMING)
    assert not rec.is_terminal


# --- RunStatusResponse mapping ---
def test_status_response_download_gating_completed():
    rec = RunRecord(run_id="r", app_id="u", upload_id="u", status=AppStatus.COMPLETED,
                    artifact_key="results/r/modernized.zip", report_key="results/r/change_report.json")
    resp = RunStatusResponse.from_record(rec)
    assert resp.download_available is True
    assert resp.report_available is True


def test_status_response_download_gated_when_not_completed():
    rec = RunRecord(run_id="r", app_id="u", upload_id="u", status=AppStatus.TRANSFORMING,
                    artifact_key="results/r/modernized.zip")
    resp = RunStatusResponse.from_record(rec)
    assert resp.download_available is False


def test_change_report_defaults():
    cr = ChangeReport(run_id="r")
    assert cr.files_added == [] and cr.files_modified == [] and cr.files_deleted == []
