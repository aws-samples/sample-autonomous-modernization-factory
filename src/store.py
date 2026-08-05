"""Run_Store - DynamoDB persistence for modernization runs (Requirement 9).

Wraps the existing `state` table (hash_key=app_id, range_key=run_id). Web-app
runs use the convention ``app_id == run_id`` so each run is a distinct partition
and is directly addressable by run_id via GetItem.

Semantics:
- put_run: conditional put that never overwrites an existing run (Req 9.2, Property 3).
- get_run: direct GetItem by run_id.
- update_status: UpdateItem that only mutates the target record; on failure the
  prior record is left unchanged (Req 9.4). Sets TTL on terminal status (Req 9.7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from .models import AppStatus
from .web_models import RunRecord, TERMINAL_STATUSES, RETENTION_DAYS


class RunStoreError(Exception):
    """Raised when a Run_Store operation fails."""


class RunAlreadyExistsError(RunStoreError):
    """Raised when creating a run whose id already exists."""


def _undecimal(obj):
    """Recursively convert DynamoDB Decimals back to int/float for Pydantic."""
    if isinstance(obj, list):
        return [_undecimal(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _undecimal(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """DynamoDB-backed store for RunRecords."""

    def __init__(self, table_name: str, region: str = "us-east-1", *, resource=None):
        self._resource = resource or boto3.resource("dynamodb", region_name=region)
        self._table = self._resource.Table(table_name)

    def put_run(self, record: RunRecord) -> None:
        """Create a run record, failing if one with the same run_id exists.

        Requirement 9.2: on failure, the run is not reported as created.
        Property 3: never overwrites a different run's record.
        """
        item = record.to_item()
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(run_id)",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise RunAlreadyExistsError(f"run {record.run_id} already exists") from exc
            raise RunStoreError(f"failed to persist run {record.run_id}: {exc}") from exc

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Fetch a run by id (Req 9.5); returns None if absent (Req 9.6)."""
        try:
            resp = self._table.get_item(Key={"app_id": run_id, "run_id": run_id})
        except ClientError as exc:
            raise RunStoreError(f"failed to read run {run_id}: {exc}") from exc
        item = resp.get("Item")
        if not item:
            return None
        return RunRecord.from_item(_undecimal(item))

    def update_status(
        self,
        run_id: str,
        status: AppStatus,
        **fields,
    ) -> None:
        """Update a run's status (and optional fields) in place (Req 9.3).

        Only sets provided, non-None fields. Sets TTL when the status is
        terminal (Req 9.7). Requires the record to exist; on failure the prior
        record is left unchanged (Req 9.4).
        """
        updates: dict = {"status": status.value if isinstance(status, AppStatus) else status,
                         "updated_at": _now_iso()}
        for key, value in fields.items():
            if value is not None:
                updates[key] = value

        if (status if isinstance(status, AppStatus) else AppStatus(status)) in TERMINAL_STATUSES:
            from datetime import timedelta

            expiry = datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
            updates["ttl"] = int(expiry.timestamp())

        set_parts = []
        names: dict = {}
        values: dict = {}
        for i, (key, value) in enumerate(updates.items()):
            names[f"#k{i}"] = key
            values[f":v{i}"] = value
            set_parts.append(f"#k{i} = :v{i}")

        try:
            self._table.update_item(
                Key={"app_id": run_id, "run_id": run_id},
                UpdateExpression="SET " + ", ".join(set_parts),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(run_id)",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise RunStoreError(f"run {run_id} not found for update") from exc
            raise RunStoreError(f"failed to update run {run_id}: {exc}") from exc
