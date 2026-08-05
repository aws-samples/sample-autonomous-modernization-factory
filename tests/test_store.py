"""Tests for src/store.py - Run_Store on DynamoDB (Task 5.2, 5.3).

Uses moto to mock DynamoDB. Covers Requirements 9.1, 9.2, 9.4, 9.6, 9.7 and
design Property 3 (run id uniqueness).

Run with: pytest tests/test_store.py -v
"""

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import AppStatus
from src.store import RunStore, RunStoreError, RunAlreadyExistsError
from src.web_models import RunRecord

TABLE = "mod-factory-state-test"


def _create_table(region="us-east-1"):
    ddb = boto3.resource("dynamodb", region_name=region)
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[
            {"AttributeName": "app_id", "KeyType": "HASH"},
            {"AttributeName": "run_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "app_id", "AttributeType": "S"},
            {"AttributeName": "run_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb


def _record(run_id="r1", **kw):
    # web-app convention: app_id == run_id
    return RunRecord(run_id=run_id, app_id=run_id, upload_id="u1", **kw)


@mock_aws
def test_put_and_get():
    _create_table()
    store = RunStore(TABLE)
    store.put_run(_record("r1", source_key="uploads/u1/source.zip"))
    got = store.get_run("r1")
    assert got is not None
    assert got.run_id == "r1"
    assert got.status == AppStatus.QUEUED
    assert got.source_key == "uploads/u1/source.zip"


@mock_aws
def test_get_missing_returns_none():
    _create_table()
    store = RunStore(TABLE)
    assert store.get_run("nope") is None


@mock_aws
def test_put_duplicate_rejected():
    _create_table()
    store = RunStore(TABLE)
    store.put_run(_record("r1"))
    with pytest.raises(RunAlreadyExistsError):
        store.put_run(_record("r1"))


@mock_aws
def test_update_status_and_fields():
    _create_table()
    store = RunStore(TABLE)
    store.put_run(_record("r1"))
    store.update_status("r1", AppStatus.TRANSFORMING, build_id="b-123")
    got = store.get_run("r1")
    assert got.status == AppStatus.TRANSFORMING
    assert got.build_id == "b-123"


@mock_aws
def test_update_terminal_sets_ttl():
    _create_table()
    store = RunStore(TABLE)
    store.put_run(_record("r1"))
    store.update_status("r1", AppStatus.COMPLETED, artifact_key="results/r1/modernized.zip")
    got = store.get_run("r1")
    assert got.status == AppStatus.COMPLETED
    assert got.ttl is not None and got.ttl > 0


@mock_aws
def test_update_missing_raises_and_creates_nothing():
    _create_table()
    store = RunStore(TABLE)
    with pytest.raises(RunStoreError):
        store.update_status("ghost", AppStatus.FAILED, error_message="x")
    assert store.get_run("ghost") is None


@mock_aws
def test_run_id_uniqueness_property():
    """Property 3: repeated creates never overwrite an existing distinct run."""
    _create_table()
    store = RunStore(TABLE)
    store.put_run(_record("r1", source_key="first"))
    # a second create with the same id must fail, leaving the original intact
    with pytest.raises(RunAlreadyExistsError):
        store.put_run(_record("r1", source_key="second"))
    got = store.get_run("r1")
    assert got.source_key == "first"
