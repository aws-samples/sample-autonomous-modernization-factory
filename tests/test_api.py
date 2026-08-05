"""Tests for src/api.py - API_Service (Task 9.3, 9.4).

FastAPI TestClient + moto + fake CodeBuild. Covers Requirements 6.2, 6.3,
8.5, 8.7 and design Property 5 (download gating).

Run with: pytest tests/test_api.py -v
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api import Deps, create_app
from src.models import AppStatus
from src.webconfig import Settings

ARTIFACTS = "mf-artifacts-test"
RESULTS = "mf-results-test"
TABLE = "mf-state-test"


class FakeCodeBuild:
    def __init__(self, status="IN_PROGRESS"):
        self.status = status

    def start_build(self, **kwargs):
        return {"build": {"id": "build-abc"}}

    def batch_get_builds(self, ids):
        return {"builds": [{"id": ids[0], "buildStatus": self.status}]}


def _aws():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=ARTIFACTS)
    s3.create_bucket(Bucket=RESULTS)
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "app_id", "KeyType": "HASH"},
                   {"AttributeName": "run_id", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "app_id", "AttributeType": "S"},
                              {"AttributeName": "run_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return s3, ddb


def _settings():
    return Settings(
        artifacts_bucket=ARTIFACTS, results_bucket=RESULTS, state_table=TABLE,
        aws_region="us-east-1", transformer_codebuild_project="transformer",
    ).validate()


def _client(cb):
    s3, ddb = _aws()
    deps = Deps(_settings(), s3_client=s3, codebuild_client=cb, dynamodb_resource=ddb)
    return TestClient(create_app(deps)), s3, deps


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", b"print('hi')\n")
    buf.seek(0)
    return buf


def _java_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pom.xml", b"<project/>\n")
        zf.writestr("src/main/java/App.java", b"public class App {}\n")
    buf.seek(0)
    return buf


@mock_aws
def test_upload_success():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.post("/api/uploads", files={"file": ("code.zip", _zip(), "application/zip")})
    assert r.status_code == 201
    assert "upload_id" in r.json()


@mock_aws
def test_upload_rejects_non_zip():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.post("/api/uploads", files={"file": ("code.txt", io.BytesIO(b"x"), "text/plain")})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_zip"


@mock_aws
def test_start_run_flow():
    client, s3, _ = _client(FakeCodeBuild())
    uid = client.post("/api/uploads", files={"file": ("code.zip", _zip(), "application/zip")}).json()["upload_id"]
    r = client.post("/api/runs", json={"upload_id": uid, "target_runtime": "python3.12"})
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"].startswith("run-")
    assert body["status"] == AppStatus.TRANSFORMING.value


@mock_aws
def test_start_run_unknown_upload():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.post("/api/runs", json={"upload_id": "nope", "target_runtime": "python3.12"})
    assert r.status_code == 404


@mock_aws
def test_start_run_unknown_transformation_422():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.post("/api/runs", json={"upload_id": "u", "transformation_id": "does-not-exist"})
    assert r.status_code == 422  # schema validation against the catalog


@mock_aws
def test_start_run_java_transformation_flow():
    client, s3, _ = _client(FakeCodeBuild())
    uid = client.post("/api/uploads", files={"file": ("code.zip", _java_zip(), "application/zip")}).json()["upload_id"]
    r = client.post("/api/runs", json={"upload_id": uid, "transformation_id": "java-aws-sdk-v1-to-v2"})
    assert r.status_code == 202
    assert r.json()["status"] == AppStatus.TRANSFORMING.value


@mock_aws
def test_start_run_language_mismatch_rejected():
    """Python code selected for a Java transformation must be rejected up front."""
    client, s3, _ = _client(FakeCodeBuild())
    uid = client.post("/api/uploads", files={"file": ("code.zip", _zip(), "application/zip")}).json()["upload_id"]
    r = client.post("/api/runs", json={"upload_id": uid, "transformation_id": "java-version-upgrade"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "language_mismatch"


@mock_aws
def test_transformations_catalog_endpoint():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.get("/api/transformations")
    assert r.status_code == 200
    data = r.json()
    ids = [t["id"] for t in data["transformations"]]
    assert "python-version-upgrade" in ids and "java-version-upgrade" in ids
    assert data["default"] == "python-version-upgrade"


@mock_aws
def test_status_unknown_run_404():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.get("/api/runs/run-deadbeef")
    assert r.status_code == 404


@mock_aws
def test_status_malformed_id_400():
    client, s3, _ = _client(FakeCodeBuild())
    r = client.get("/api/runs/!!bad!!")
    assert r.status_code == 400


@mock_aws
def test_unknown_route_404():
    client, s3, _ = _client(FakeCodeBuild())
    assert client.get("/api/does-not-exist").status_code == 404


@mock_aws
def test_openapi_published():
    """Req 8.8 - machine-readable API description is published."""
    client, s3, _ = _client(FakeCodeBuild())
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "/api/uploads" in spec["paths"]
    assert "/api/runs" in spec["paths"]


@mock_aws
def test_download_gating_property():
    """Property 5: download only available when COMPLETED."""
    cb = FakeCodeBuild(status="IN_PROGRESS")
    client, s3, deps = _client(cb)
    uid = client.post("/api/uploads", files={"file": ("code.zip", _zip(), "application/zip")}).json()["upload_id"]
    run_id = client.post("/api/runs", json={"upload_id": uid}).json()["run_id"]

    # Not completed yet -> 409
    assert client.get(f"/api/runs/{run_id}/download").status_code == 409

    # Complete it
    cb.status = "SUCCEEDED"
    s3.put_object(Bucket=RESULTS, Key=f"results/{run_id}/result.json",
                  Body=json.dumps({"status": "COMPLETED",
                                   "artifact_key": f"results/{run_id}/modernized.zip",
                                   "report_key": f"results/{run_id}/change_report.json"}).encode())
    s3.put_object(Bucket=RESULTS, Key=f"results/{run_id}/modernized.zip", Body=b"zipbytes")
    # trigger status refresh
    assert client.get(f"/api/runs/{run_id}").json()["status"] == AppStatus.COMPLETED.value

    dl = client.get(f"/api/runs/{run_id}/download")
    assert dl.status_code == 200
    assert "download_url" in dl.json()


@mock_aws
def test_report_endpoint():
    cb = FakeCodeBuild(status="SUCCEEDED")
    client, s3, deps = _client(cb)
    uid = client.post("/api/uploads", files={"file": ("code.zip", _zip(), "application/zip")}).json()["upload_id"]
    run_id = client.post("/api/runs", json={"upload_id": uid}).json()["run_id"]
    s3.put_object(Bucket=RESULTS, Key=f"results/{run_id}/result.json",
                  Body=json.dumps({"status": "COMPLETED",
                                   "artifact_key": f"results/{run_id}/modernized.zip",
                                   "report_key": f"results/{run_id}/change_report.json"}).encode())
    s3.put_object(Bucket=RESULTS, Key=f"results/{run_id}/change_report.json",
                  Body=json.dumps({"run_id": run_id, "files_added": ["a.py"],
                                   "files_modified": [], "files_deleted": [],
                                   "summary": "1 added"}).encode())
    client.get(f"/api/runs/{run_id}")  # refresh -> COMPLETED
    rep = client.get(f"/api/runs/{run_id}/report")
    assert rep.status_code == 200
    assert rep.json()["files_added"] == ["a.py"]
