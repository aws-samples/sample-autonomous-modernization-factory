"""Run_Manager - orchestrates a modernization run (Requirements 4, 5, 11).

Responsibilities:
- create_run: validate the upload / runtime / credentials, persist a QUEUED
  record, and start the CodeBuild transformer asynchronously.
- refresh_status: map the CodeBuild build state to a Run_Status, enforce the
  transformation timeout, and read the terminal result from S3.

The actual code transformation runs inside CodeBuild (the ATX CLI); this module
never executes customer code. It delegates the transform stage instead of the
local regex rewrites.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from .models import AppStatus
from .store import RunStore, RunStoreError
from .upload import UploadHandler
from .web_models import RunRecord, TERMINAL_STATUSES
from .webconfig import Settings
from . import catalog

# Terminal CodeBuild build statuses.
_CB_TERMINAL = {"SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"}

# Curated, customer-facing messages for state-machine milestones. Keyed by
# (transition, state_name); anything not listed is intentionally hidden so the
# progress feed stays high-level and never leaks internal/build detail.
_PROGRESS_MESSAGES = {
    ("entered", "Transform"): "Analyzing and transforming your code",
    ("exited", "Transform"): "Code transformation finished",
    ("entered", "Validate"): "Validating the transformed code (build and tests)",
    ("exited", "Validate"): "Validation finished",
    ("entered", "Remediate"): "Auto-remediating build/test failures with AI",
    ("exited", "Remediate"): "Applied automated fixes - re-validating",
    ("entered", "Completed"): "Completed - your modernized code is ready",
    ("entered", "Escalated"): "Flagged for human review",
    ("entered", "Failed"): "Transformation could not be completed",
}


class RunManagerError(Exception):
    """Raised for run-management failures surfaced to the API layer."""


class TransformNotConfiguredError(RunManagerError):
    """Raised when transformation credentials/config are missing (Req 11.11)."""


class UploadNotFoundError(RunManagerError):
    """Raised when starting a run for an unknown upload (Req 4.4)."""


class LanguageMismatchError(RunManagerError):
    """Raised when the uploaded code doesn't match the transformation's language."""


class RunManager:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        uploads: UploadHandler,
        *,
        codebuild_client=None,
        s3_client=None,
        sfn_client=None,
    ):
        self._s = settings
        self._store = store
        self._uploads = uploads
        self._cb = codebuild_client or boto3.client("codebuild", region_name=settings.aws_region)
        self._s3 = s3_client or boto3.client("s3", region_name=settings.aws_region)
        # Step Functions client is created lazily only when orchestration is
        # configured, so the direct-CodeBuild path needs no SFN dependency.
        self._sfn = sfn_client
        if self._sfn is None and settings.state_machine_arn:
            self._sfn = boto3.client("stepfunctions", region_name=settings.aws_region)

    @property
    def _use_orchestration(self) -> bool:
        """Tier 1: drive runs through the Step Functions state machine."""
        return bool(self._s.state_machine_arn)

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #
    def create_run(self, upload_id: str, transformation_id: Optional[str] = None,
                   target: Optional[str] = None) -> RunRecord:
        """Create and start a Modernization_Run for a chosen transformation."""
        transformation_id = transformation_id or catalog.DEFAULT_TRANSFORMATION_ID
        entry = catalog.get(transformation_id)
        if entry is None:
            raise ValueError(f"unknown transformation_id '{transformation_id}'")

        # Reject if the transform service is not configured.
        if not self._s.transform_configured:
            raise TransformNotConfiguredError("transformation service is not configured")

        # Upload must exist.
        if not self._uploads.upload_exists(upload_id):
            raise UploadNotFoundError(f"upload {upload_id} not found")

        # Pre-flight language gate: reject before starting the (billable) pipeline
        # if the uploaded archive has no files matching the transformation's
        # source language (e.g. Python code selected for a Java transformation).
        exts = catalog.expected_source_exts(entry)
        if exts:
            names = self._uploads.list_entries(upload_id)
            if names and not catalog.source_matches(names, exts):
                raise LanguageMismatchError(
                    f"The '{entry.label}' transformation expects a {entry.language} "
                    f"codebase, but the uploaded archive contains no matching source "
                    f"files. Upload a {entry.language} project or pick a different "
                    f"transformation."
                )

        run_id = "run-" + _new_id()
        source_key = self._uploads.source_key(upload_id)
        result_prefix = f"results/{run_id}/"

        record = RunRecord(
            run_id=run_id,
            app_id=run_id,  # web-app convention: partition by run_id
            upload_id=upload_id,
            status=AppStatus.QUEUED,
            transformation_id=entry.id,
            transformation_name=entry.name,
            language=entry.language,
            target=target,
            source_key=source_key,
            result_prefix=result_prefix,
        )

        # Persist before reporting created.
        self._store.put_run(record)

        # If starting the run fails, mark FAILED. Tier 1 drives the closed-loop
        # state machine; otherwise start the transformer directly.
        try:
            if self._use_orchestration:
                execution_arn = self._start_execution(run_id, source_key, entry.name, target)
                self._safe_update(run_id, AppStatus.TRANSFORMING, execution_arn=execution_arn)
                record.status = AppStatus.TRANSFORMING
                record.execution_arn = execution_arn
            else:
                build_id = self._start_build(run_id, source_key, entry.name, target)
                self._safe_update(run_id, AppStatus.TRANSFORMING, build_id=build_id)
                record.status = AppStatus.TRANSFORMING
                record.build_id = build_id
        except Exception as exc:  # noqa: BLE001 - surface as run failure
            self._safe_update(run_id, AppStatus.FAILED,
                              error_message=f"failed to start transform run: {exc}")
            record.status = AppStatus.FAILED
            record.error_message = str(exc)[:1000]
        return record

    def _start_execution(self, run_id: str, source_key: str,
                         transformation_name: str, target: Optional[str]) -> str:
        """Start a Step Functions execution for the closed-loop pipeline.

        The state machine's Transform step passes RUN_ID/SOURCE_KEY plus the
        chosen TRANSFORMATION_NAME and optional TARGET to the transformer
        CodeBuild project, then validates the output.
        """
        resp = self._sfn.start_execution(
            stateMachineArn=self._s.state_machine_arn,
            name=run_id,
            input=json.dumps({
                "run_id": run_id,
                "app_id": run_id,
                "source_key": source_key,
                "transformation_name": transformation_name,
                "target": target or "",
                # Closed-loop remediation counter (Validate -> Remediate -> Validate).
                "attempt": 0,
            }),
        )
        return resp["executionArn"]

    def _start_build(self, run_id: str, source_key: str,
                     transformation_name: str, target: Optional[str]) -> str:
        resp = self._cb.start_build(
            projectName=self._s.transformer_codebuild_project,
            environmentVariablesOverride=[
                {"name": "RUN_ID", "value": run_id, "type": "PLAINTEXT"},
                {"name": "SOURCE_KEY", "value": source_key, "type": "PLAINTEXT"},
                {"name": "TARGET", "value": target or "", "type": "PLAINTEXT"},
                {"name": "TRANSFORMATION_NAME", "value": transformation_name, "type": "PLAINTEXT"},
                {"name": "RESULTS_BUCKET", "value": self._s.results_bucket, "type": "PLAINTEXT"},
            ],
        )
        return resp["build"]["id"]

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self._store.get_run(run_id)

    def get_progress_events(self, record: RunRecord) -> list[dict]:
        """Return a curated, timestamped progress timeline for the UI.

        Merges high-level Step Functions milestones (see _PROGRESS_MESSAGES) with
        the finer-grained, hand-authored markers the CodeBuild builds stream to
        results/<run_id>/progress.log. Only curated strings are ever returned -
        never raw tool output, code, or paths. Sorted by time.
        """
        events = self._sfn_progress(record) + self._read_progress_markers(record)
        events.sort(key=lambda e: e.get("time") or "")
        deduped: list[dict] = []
        for e in events:
            if deduped and deduped[-1] == e:
                continue
            deduped.append(e)
        return deduped

    def _sfn_progress(self, record: RunRecord) -> list[dict]:
        """High-level milestones from the Step Functions execution history."""
        if not record.execution_arn or self._sfn is None:
            return []
        try:
            resp = self._sfn.get_execution_history(
                executionArn=record.execution_arn,
                maxResults=1000,
                includeExecutionData=False,
            )
        except ClientError:
            return []

        events: list[dict] = []
        for ev in resp.get("events", []):
            message = "Run started" if ev.get("type") == "ExecutionStarted" else None
            entered = ev.get("stateEnteredEventDetails")
            if entered:
                message = _PROGRESS_MESSAGES.get(("entered", entered.get("name")), message)
            exited = ev.get("stateExitedEventDetails")
            if exited:
                message = _PROGRESS_MESSAGES.get(("exited", exited.get("name")), message)
            if not message:
                continue
            ts = ev.get("timestamp")
            events.append({
                "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else "",
                "message": message,
            })
        return events

    def _read_progress_markers(self, record: RunRecord) -> list[dict]:
        """Fine-grained sub-step markers streamed by the builds to S3.

        Each line is "HH:MM:SS|message"; only these hand-authored strings are
        surfaced. A missing/unreadable log is non-fatal (returns []).
        """
        prefix = record.result_prefix
        if not prefix or not self._s.results_bucket:
            return []
        try:
            obj = self._s3.get_object(
                Bucket=self._s.results_bucket, Key=f"{prefix}progress.log"
            )
            text = obj["Body"].read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - missing/unreadable log is non-fatal
            return []
        out: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            t, msg = line.split("|", 1)
            out.append({"time": t.strip(), "message": msg.strip()})
        return out

    def refresh_status(self, run_id: str) -> Optional[RunRecord]:
        """Reconcile the stored status with the CodeBuild build (Req 5.2–5.5, 11.7–11.10, 11.12).

        Property 4: never moves a terminal run back to a non-terminal status.
        """
        record = self._store.get_run(run_id)
        if record is None:
            return None
        if record.status in TERMINAL_STATUSES:
            return record  # terminal is final

        # Tier 1: orchestrated runs are reconciled against the state machine.
        # NOTE: the wall-clock timeout is only applied to runs that are still
        # actually running (below / in _refresh_via_execution), never before
        # consulting the execution - otherwise a run that completed long ago but
        # is first polled after the timeout window would be falsely failed.
        if record.execution_arn:
            return self._refresh_via_execution(record)

        if not record.build_id:
            return record

        try:
            builds = self._cb.batch_get_builds(ids=[record.build_id]).get("builds", [])
        except ClientError:
            return record  # transient; leave as-is
        if not builds:
            return record
        build_status = builds[0].get("buildStatus", "IN_PROGRESS")

        if build_status not in _CB_TERMINAL:
            # Still building - enforce the transformation timeout here only.
            if self._timed_out(record):
                self._safe_update(run_id, AppStatus.FAILED,
                                  error_message="transformation timed out")
                return self._store.get_run(run_id)
            if record.status != AppStatus.TRANSFORMING:
                self._safe_update(run_id, AppStatus.TRANSFORMING)
                record.status = AppStatus.TRANSFORMING
            return record

        # Build finished - resolve the terminal outcome from the result file.
        if build_status == "SUCCEEDED":
            return self._apply_result(record)

        # Non-success CodeBuild state -> FAILED (Req 11.9).
        self._safe_update(run_id, AppStatus.FAILED,
                          error_message=f"transform build ended with status {build_status}")
        return self._store.get_run(run_id)

    def _refresh_via_execution(self, record: RunRecord) -> Optional[RunRecord]:
        """Reconcile a run's status with its Step Functions execution (Tier 1).

        Execution output carries {"outcome": COMPLETED|ESCALATED|FAILED}. On a
        COMPLETED outcome the transformer's result.json is authoritative for the
        artifact/report keys (and can itself downgrade to ESCALATED/FAILED); a
        validation-failure outcome forces ESCALATED; anything else is FAILED.
        """
        try:
            desc = self._sfn.describe_execution(executionArn=record.execution_arn)
        except ClientError:
            return record  # transient; leave as-is

        exec_status = desc.get("status", "RUNNING")
        if exec_status == "RUNNING":
            # Enforce the transformation timeout only while still running.
            if self._timed_out(record):
                self._safe_update(record.run_id, AppStatus.FAILED,
                                  error_message="transformation timed out")
                return self._store.get_run(record.run_id)
            if record.status != AppStatus.TRANSFORMING:
                self._safe_update(record.run_id, AppStatus.TRANSFORMING)
                record.status = AppStatus.TRANSFORMING
            return record

        if exec_status == "SUCCEEDED":
            try:
                output = json.loads(desc.get("output") or "{}")
            except (ValueError, TypeError):
                output = {}
            outcome = (output.get("outcome") or "").upper()
            if outcome == "COMPLETED":
                return self._apply_result(record)
            if outcome == "ESCALATED":
                self._safe_update(
                    record.run_id, AppStatus.ESCALATED,
                    escalation_reason=output.get("reason") or "requires human review",
                )
                return self._store.get_run(record.run_id)
            self._safe_update(
                record.run_id, AppStatus.FAILED,
                error_message=output.get("reason") or "transformation failed",
            )
            return self._store.get_run(record.run_id)

        # Execution FAILED / TIMED_OUT / ABORTED -> FAILED (Req 11.9).
        reason = desc.get("cause") or desc.get("error") or f"execution {exec_status.lower()}"
        self._safe_update(record.run_id, AppStatus.FAILED, error_message=str(reason)[:1000])
        return self._store.get_run(record.run_id)

    def _apply_result(self, record: RunRecord) -> Optional[RunRecord]:
        """Read results/<run_id>/result.json and set the terminal status."""
        result = self._read_result(record.result_prefix)
        if result is None:
            # Build succeeded but no result file - treat as failure to be safe.
            self._safe_update(record.run_id, AppStatus.FAILED,
                              error_message="transform produced no result manifest")
            return self._store.get_run(record.run_id)

        status_str = (result.get("status") or "").upper()
        if status_str == "ESCALATED":
            self._safe_update(
                record.run_id, AppStatus.ESCALATED,
                escalation_reason=result.get("escalation_reason") or "requires human review",
            )
        elif status_str == "COMPLETED":
            self._safe_update(
                record.run_id, AppStatus.COMPLETED,
                artifact_key=result.get("artifact_key"),
                report_key=result.get("report_key"),
            )
        else:
            self._safe_update(
                record.run_id, AppStatus.FAILED,
                error_message=result.get("error_message") or "transform failed",
            )
        return self._store.get_run(record.run_id)

    def _read_result(self, result_prefix: Optional[str]) -> Optional[dict]:
        if not result_prefix:
            return None
        key = f"{result_prefix}result.json"
        try:
            obj = self._s3.get_object(Bucket=self._s.results_bucket, Key=key)
            return json.loads(obj["Body"].read())
        except (ClientError, ValueError, KeyError):
            return None

    def _timed_out(self, record: RunRecord) -> bool:
        started = record.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return elapsed > self._s.transformation_timeout_min * 60

    def _safe_update(self, run_id: str, status: AppStatus, **fields) -> None:
        try:
            self._store.update_status(run_id, status, **fields)
        except RunStoreError:
            # Requirement 9.4 - prior record retained; swallow to avoid masking.
            pass


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]
