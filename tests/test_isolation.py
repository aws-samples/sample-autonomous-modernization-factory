"""Isolation checks (Task 11.3) - design Property 7.

Validates that each run maps to its own storage prefix and that the transformer
CodeBuild IAM policy scopes S3 access to per-run prefixes (not whole buckets).

Run with: pytest tests/test_isolation.py -v
"""

import io
import sys
import zipfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.run_manager import RunManager
from src.store import RunStore
from src.upload import UploadHandler
from src.webconfig import Settings

ARTIFACTS = "mf-artifacts-test"
RESULTS = "mf-results-test"
TABLE = "mf-state-test"

REPO = Path(__file__).parent.parent


class FakeCodeBuild:
    def start_build(self, **kwargs):
        return {"build": {"id": "b"}}

    def batch_get_builds(self, ids):
        return {"builds": [{"id": ids[0], "buildStatus": "IN_PROGRESS"}]}


def _setup():
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
    return s3


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", b"x\n")
    buf.seek(0)
    return buf


@mock_aws
def test_runs_have_distinct_prefixes():
    """Property 7: each run/upload maps to its own, run-scoped storage prefix."""
    s3 = _setup()
    settings = Settings(artifacts_bucket=ARTIFACTS, results_bucket=RESULTS, state_table=TABLE,
                        aws_region="us-east-1", transformer_codebuild_project="t").validate()
    store = RunStore(TABLE, resource=boto3.resource("dynamodb", region_name="us-east-1"))
    uploads = UploadHandler(ARTIFACTS, max_bytes=10 * 1024 * 1024, client=s3)
    mgr = RunManager(settings, store, uploads, codebuild_client=FakeCodeBuild(), s3_client=s3)

    u1 = uploads.store_upload("a.zip", _zip())
    u2 = uploads.store_upload("b.zip", _zip())
    r1 = mgr.create_run(u1)
    r2 = mgr.create_run(u2)

    assert r1.run_id != r2.run_id
    assert r1.result_prefix != r2.result_prefix
    assert r1.result_prefix == f"results/{r1.run_id}/"
    assert uploads.source_key(u1) != uploads.source_key(u2)


def test_transformer_iam_is_prefix_scoped():
    """The transformer role must not have whole-bucket write access."""
    tf = (REPO / "modules" / "codebuild" / "transformer.tf").read_text()
    # results writes limited to results/* prefix
    assert "arn:aws:s3:::${var.results_bucket}/results/*" in tf
    # must NOT grant PutObject on the entire results bucket root
    assert "arn:aws:s3:::${var.results_bucket}/*" not in tf
    # source reads limited to uploads/scripts prefixes
    assert "arn:aws:s3:::${var.source_bucket}/uploads/*" in tf
