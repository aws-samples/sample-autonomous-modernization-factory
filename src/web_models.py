"""Pydantic models for the modernization web app (API + Run_Store).

Reuses AppStatus and UpgradeType from src/models.py. Provides the run record
persisted in DynamoDB, the API request/response models, and the change report.

Requirements: 3.3, 3.6, 5.1, 9.1, 9.7 (models + serialization + TTL).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import AppStatus, UpgradeType
from .catalog import DEFAULT_TRANSFORMATION_ID, get as catalog_get, is_valid as catalog_is_valid

# Target_Runtime format: "python" + major.minor (e.g. python3.12) - Requirement 3.6.
RUNTIME_RE = re.compile(r"^python\d+\.\d+$")

# Statuses considered terminal (for TTL / monotonicity).
TERMINAL_STATUSES = {AppStatus.COMPLETED, AppStatus.ESCALATED, AppStatus.FAILED}

# Retention after terminal status - Requirement 9.7.
RETENTION_DAYS = 30

_MAX_REASON = 1000


def _truncate(text: Optional[str], limit: int = _MAX_REASON) -> Optional[str]:
    if text is None:
        return None
    return text[:limit]


def validate_target_runtime(value: str) -> str:
    """Raise ValueError if value is not python<major>.<minor>."""
    if not RUNTIME_RE.match(value or ""):
        raise ValueError(
            "target_runtime must be 'python' followed by major.minor, e.g. python3.12"
        )
    return value


class RunRecord(BaseModel):
    """Full persisted state of a modernization run (a DynamoDB item)."""

    run_id: str
    app_id: str  # DynamoDB partition key (== upload_id for the web app)
    upload_id: str
    status: AppStatus = AppStatus.QUEUED
    target_runtime: str = "python3.12"
    upgrade_type: UpgradeType = UpgradeType.RUNTIME_UPGRADE
    # Transformation selection (catalog-driven).
    transformation_id: str = DEFAULT_TRANSFORMATION_ID
    transformation_name: Optional[str] = None  # atx -n value, e.g. "AWS/java-version-upgrade"
    language: Optional[str] = None
    target: Optional[str] = None                # target version/framework (additionalPlanContext)
    build_id: Optional[str] = None
    execution_arn: Optional[str] = None  # Step Functions execution (Tier 1 orchestration)
    source_key: Optional[str] = None
    result_prefix: Optional[str] = None
    artifact_key: Optional[str] = None
    report_key: Optional[str] = None
    escalation_reason: Optional[str] = None
    error_message: Optional[str] = None
    complexity_score: Optional[int] = None
    complexity_threshold: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl: Optional[int] = None

    @field_validator("escalation_reason", "error_message")
    @classmethod
    def _cap_reason(cls, v: Optional[str]) -> Optional[str]:
        return _truncate(v)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def with_terminal_ttl(self) -> "RunRecord":
        """Return a copy with the TTL set to RETENTION_DAYS after now (Req 9.7)."""
        expiry = datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
        return self.model_copy(update={"ttl": int(expiry.timestamp())})

    def to_item(self) -> dict:
        """Serialize to a DynamoDB-friendly item (JSON-native types)."""
        data = self.model_dump(mode="json", exclude_none=True)
        return data

    @classmethod
    def from_item(cls, item: dict) -> "RunRecord":
        """Deserialize a DynamoDB item back into a RunRecord."""
        return cls.model_validate(item)


class StartRunRequest(BaseModel):
    """Body of POST /api/runs.

    The user picks a transformation from the catalog and (optionally) a target
    version/framework. `target_runtime` is accepted as a backward-compatible
    alias for `target`.
    """

    upload_id: str
    transformation_id: str = DEFAULT_TRANSFORMATION_ID
    target: Optional[str] = None
    target_runtime: Optional[str] = None  # legacy alias for target

    @field_validator("transformation_id")
    @classmethod
    def _check_transformation(cls, v: str) -> str:
        if not catalog_is_valid(v):
            raise ValueError(f"unknown transformation_id '{v}'")
        return v

    @model_validator(mode="after")
    def _coalesce_target(self) -> "StartRunRequest":
        if not self.target and self.target_runtime:
            self.target = self.target_runtime
        return self


class ProgressEvent(BaseModel):
    """A single customer-facing progress line (high-level, no internal detail)."""

    time: str      # HH:MM:SS UTC
    message: str


class RunStatusResponse(BaseModel):
    """Response of GET /api/runs/{run_id}."""

    run_id: str
    status: AppStatus
    transformation: Optional[str] = None   # human-friendly label
    target: Optional[str] = None
    escalation_reason: Optional[str] = None
    complexity_score: Optional[int] = None
    complexity_threshold: Optional[int] = None
    error_message: Optional[str] = None
    download_available: bool = False
    report_available: bool = False
    progress: list[ProgressEvent] = Field(default_factory=list)

    @classmethod
    def from_record(cls, r: RunRecord) -> "RunStatusResponse":
        completed = r.status == AppStatus.COMPLETED
        entry = catalog_get(r.transformation_id)
        return cls(
            run_id=r.run_id,
            status=r.status,
            transformation=entry.label if entry else r.transformation_id,
            target=r.target,
            escalation_reason=r.escalation_reason,
            complexity_score=r.complexity_score,
            complexity_threshold=r.complexity_threshold,
            error_message=r.error_message,
            download_available=completed and bool(r.artifact_key),
            report_available=completed and bool(r.report_key),
        )


class ChangeReport(BaseModel):
    """The transformation change report (Requirement 12)."""

    run_id: str
    files_added: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)
    summary: str = ""
    diff_text: Optional[str] = None
    cli_report: Optional[str] = None
