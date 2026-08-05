"""API_Service - FastAPI layer for the modernization web app (Requirement 8).

Endpoints:
  POST /api/uploads                 -> upload a ZIP, returns upload_id (201)
  POST /api/runs                    -> start a run, returns run_id (202)
  GET  /api/runs/{run_id}           -> run status (RunStatusResponse)
  GET  /api/runs/{run_id}/download  -> presigned URL for the modernized artifact
  GET  /api/runs/{run_id}/report    -> the change report
  GET  /                            -> serves the frontend (if present)

The app never executes uploaded code - transformation happens in CodeBuild.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .models import AppStatus
from .run_manager import (
    RunManager,
    TransformNotConfiguredError,
    UploadNotFoundError,
    LanguageMismatchError,
)
from .store import RunStore
from .upload import UploadHandler, UploadValidationError
from .web_models import ChangeReport, RunStatusResponse, StartRunRequest
from .webconfig import Settings, load_settings
from . import catalog

_RUN_ID_RE = re.compile(r"^run-[0-9a-f]{6,}$")
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class Deps:
    """Bundle of injectable service dependencies (eases testing)."""

    def __init__(self, settings: Settings, *, s3_client=None, codebuild_client=None,
                 dynamodb_resource=None, sfn_client=None):
        self.settings = settings
        # SigV4 is required to presign GETs for KMS-encrypted (SSE-KMS) objects;
        # without it boto3 can emit a SigV2 URL that S3 rejects with
        # "requests specifying Server Side Encryption with AWS KMS ... require
        # AWS Signature Version 4".
        self.s3 = s3_client or boto3.client(
            "s3",
            region_name=settings.aws_region,
            config=Config(signature_version="s3v4"),
        )
        self.store = RunStore(
            settings.state_table, settings.aws_region,
            resource=dynamodb_resource,
        ) if settings.state_table else RunStore.__new__(RunStore)
        self.uploads = UploadHandler(
            settings.artifacts_bucket, settings.aws_region,
            max_bytes=settings.max_upload_bytes, client=self.s3,
        )
        self.run_manager = RunManager(
            settings, self.store, self.uploads,
            codebuild_client=codebuild_client, s3_client=self.s3,
            sfn_client=sfn_client,
        )


class StartRunResponse(BaseModel):
    run_id: str
    status: AppStatus


def _validate_run_id(run_id: str) -> None:
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="invalid run identifier")


def create_app(deps: Optional[Deps] = None, settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI app. Tests inject a preconfigured ``deps``."""
    settings = settings or (deps.settings if deps else load_settings())
    if deps is None:
        deps = Deps(settings)

    app = FastAPI(
        title="Modernization Web App",
        version="0.1.0",
        description="Upload a codebase, transform it via AWS Transform (ATX CLI) in CodeBuild, download the result.",
    )

    def get_deps() -> Deps:
        return deps

    # --- Upload (Req 1, 8.1) ---
    @app.post("/api/uploads", status_code=201)
    def upload(file: UploadFile = File(...), d: Deps = Depends(get_deps)):
        try:
            upload_id = d.uploads.store_upload(file.filename, file.file)
        except UploadValidationError as exc:
            status = 413 if exc.code == "too_large" else 400
            raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})
        return {"upload_id": upload_id}

    # --- Start run (Req 4, 8.2) ---
    # --- Transformation catalog (drives the UI dropdown) ---
    @app.get("/api/transformations")
    def list_transformations():
        return {
            "default": catalog.DEFAULT_TRANSFORMATION_ID,
            "transformations": [t.model_dump() for t in catalog.all_transformations()],
        }

    @app.post("/api/runs", status_code=202, response_model=StartRunResponse)
    def start_run(body: StartRunRequest, d: Deps = Depends(get_deps)):
        try:
            record = d.run_manager.create_run(
                body.upload_id, body.transformation_id, body.target,
            )
        except UploadNotFoundError:
            raise HTTPException(status_code=404, detail="upload not found")
        except LanguageMismatchError as exc:
            raise HTTPException(status_code=400, detail={"code": "language_mismatch", "message": str(exc)})
        except TransformNotConfiguredError:
            raise HTTPException(status_code=503, detail="transformation service is not configured")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return StartRunResponse(run_id=record.run_id, status=record.status)

    # --- Status (Req 5, 8.3) ---
    @app.get("/api/runs/{run_id}", response_model=RunStatusResponse)
    def get_status(run_id: str, d: Deps = Depends(get_deps)):
        _validate_run_id(run_id)
        record = d.run_manager.refresh_status(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        resp = RunStatusResponse.from_record(record)
        resp.progress = d.run_manager.get_progress_events(record)
        return resp

    # --- Download (Req 6, 8.4) ---
    @app.get("/api/runs/{run_id}/download")
    def download(run_id: str, d: Deps = Depends(get_deps)):
        _validate_run_id(run_id)
        record = d.run_manager.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        if record.status != AppStatus.COMPLETED or not record.artifact_key:
            raise HTTPException(status_code=409, detail="modernized artifact is not yet available")
        try:
            url = d.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": d.settings.results_bucket, "Key": record.artifact_key},
                ExpiresIn=d.settings.presign_expiry_s,
            )
        except Exception:
            raise HTTPException(status_code=500, detail="the download could not be produced")
        return {"download_url": url}

    # --- Change report (Req 12.5–12.7) ---
    @app.get("/api/runs/{run_id}/report", response_model=ChangeReport)
    def get_report(run_id: str, d: Deps = Depends(get_deps)):
        _validate_run_id(run_id)
        record = d.run_manager.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        if record.status != AppStatus.COMPLETED or not record.report_key:
            raise HTTPException(status_code=409, detail="change report is not yet available")
        try:
            obj = d.s3.get_object(Bucket=d.settings.results_bucket, Key=record.report_key)
            data = json.loads(obj["Body"].read())
        except Exception:
            raise HTTPException(status_code=500, detail="the change report could not be retrieved")
        return ChangeReport(**data)

    # --- Frontend (optional static mount) ---
    if _FRONTEND_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

    return app


# Module-level app for `uvicorn src.api:app` (requires env-configured settings).
try:  # pragma: no cover - only when fully configured
    app = create_app()
except Exception:  # pragma: no cover - allow import without AWS configured
    app = None
