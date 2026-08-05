"""Configuration for the modernization web app (API_Service, Run_Manager, storage).

Settings are read from environment variables with sensible defaults so the app
can boot locally without AWS. Limits and ranges match the requirements/design.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# AWS Transform custom supported regions (design: Region availability).
AWS_TRANSFORM_REGIONS: tuple[str, ...] = (
    "us-east-1",
    "eu-central-1",
    "eu-west-2",
    "ca-central-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-southeast-2",
    "ap-south-1",
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the web app.

    Bounds (min/max) mirror the acceptance-criteria ranges in the requirements.
    """

    # --- Storage / AWS ---
    artifacts_bucket: str = ""
    results_bucket: str = ""
    state_table: str = ""
    aws_region: str = "us-east-1"

    # --- Transform (CodeBuild + ATX CLI) ---
    transformer_codebuild_project: str = ""

    # --- Orchestration (Step Functions closed loop) ---
    state_machine_arn: str = ""  # if set, runs are driven via the state machine

    # --- Upload / extraction limits (Requirements 1, 2) ---
    max_upload_bytes: int = 100 * 1024 * 1024          # 100 MB
    extract_max_bytes: int = 500 * 1024 * 1024         # 500 MB (range 1MB–5000MB)
    extract_max_entries: int = 10_000                  # range 1–100000
    extract_timeout_s: int = 60                        # range 1–600

    # --- Run behavior (Requirement 11) ---
    transformation_timeout_min: int = 30               # range 1–120
    presign_expiry_s: int = 900                        # 15 min

    # --- Defaults (Requirement 3) ---
    default_target_runtime: str = "python3.12"

    def validate(self) -> "Settings":
        """Return a copy with numeric limits clamped to their allowed ranges."""
        return Settings(
            artifacts_bucket=self.artifacts_bucket,
            results_bucket=self.results_bucket,
            state_table=self.state_table,
            aws_region=self.aws_region,
            transformer_codebuild_project=self.transformer_codebuild_project,
            state_machine_arn=self.state_machine_arn,
            max_upload_bytes=max(1, self.max_upload_bytes),
            extract_max_bytes=_clamp(self.extract_max_bytes, 1 * 1024 * 1024, 5000 * 1024 * 1024),
            extract_max_entries=_clamp(self.extract_max_entries, 1, 100_000),
            extract_timeout_s=_clamp(self.extract_timeout_s, 1, 600),
            transformation_timeout_min=_clamp(self.transformation_timeout_min, 1, 120),
            presign_expiry_s=max(1, self.presign_expiry_s),
            default_target_runtime=self.default_target_runtime,
        )

    @property
    def transform_configured(self) -> bool:
        """True when the settings needed to run a real transform are present.

        Used to satisfy Requirement 11.11 (reject start when the transformation
        service is not configured). The specific transformation is chosen per run
        from the catalog, so only the transformer CodeBuild project is required.
        """
        return bool(self.transformer_codebuild_project)

    @property
    def region_supported(self) -> bool:
        return self.aws_region in AWS_TRANSFORM_REGIONS


def load_settings() -> Settings:
    """Build Settings from environment variables (with defaults), clamped to range."""
    s = Settings(
        artifacts_bucket=os.environ.get("MF_ARTIFACTS_BUCKET", ""),
        results_bucket=os.environ.get("MF_RESULTS_BUCKET", ""),
        state_table=os.environ.get("MF_STATE_TABLE", ""),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        transformer_codebuild_project=os.environ.get("MF_TRANSFORMER_PROJECT", ""),
        state_machine_arn=os.environ.get("MF_STATE_MACHINE_ARN", ""),
        max_upload_bytes=_env_int("MF_MAX_UPLOAD_BYTES", 100 * 1024 * 1024),
        extract_max_bytes=_env_int("MF_EXTRACT_MAX_BYTES", 500 * 1024 * 1024),
        extract_max_entries=_env_int("MF_EXTRACT_MAX_ENTRIES", 10_000),
        extract_timeout_s=_env_int("MF_EXTRACT_TIMEOUT_S", 60),
        transformation_timeout_min=_env_int("MF_TRANSFORM_TIMEOUT_MIN", 30),
        presign_expiry_s=_env_int("MF_PRESIGN_EXPIRY_S", 900),
        default_target_runtime=os.environ.get("MF_DEFAULT_TARGET_RUNTIME", "python3.12"),
    ).validate()
    return s
