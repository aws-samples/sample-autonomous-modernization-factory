"""AI remediation agent for the closed-loop modernization pipeline.

Runs inside the ``remediator`` CodeBuild project after a failed validation
(build/test). It reads the validation failure log plus the relevant source
files from the modernized code, asks Amazon Bedrock (Claude) for targeted
fixes, and applies them to the working tree so the state machine can
re-validate.

Contract (used by the Step Functions loop):
  - exit 0  -> at least one fix was applied; the buildspec re-packages the code
              and the pipeline re-validates.
  - exit !=0 -> no usable fix could be produced (or an error); the state
              machine's Remediate ``Catch`` routes the run to human escalation.

Environment:
  RUN_ID, RESULTS_BUCKET, BEDROCK_MODEL_ID, AWS_REGION (optional).
Working directory: the extracted modernized code (CodeBuild S3 source root).

Only hand-authored, high-level progress strings are surfaced to customers; raw
model output and code never leave this sandbox except as applied file edits.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Caps to keep the prompt bounded and the fix loop cheap/predictable.
_MAX_FILE_BYTES = 60_000
_MAX_TOTAL_PROMPT = 180_000
_MAX_FILES = 12
_MAX_TOKENS = 8_000

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "target", "build", ".gradle",
    "dist", ".venv", "venv", ".mvn", ".idea",
}
_TEXT_EXTS = {
    ".py", ".java", ".js", ".ts", ".jsx", ".tsx", ".xml", ".txt", ".toml",
    ".cfg", ".ini", ".gradle", ".kts", ".properties", ".json", ".yaml", ".yml",
}
# Build/config files that are almost always relevant to a build failure.
_BUILD_FILES = (
    "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt",
    "setup.py", "setup.cfg", "pyproject.toml", "package.json", "tsconfig.json",
)

_SYSTEM_PROMPT = (
    "You are an automated code remediation agent in a modernization pipeline. "
    "An automated transformation upgraded a codebase and its build/test "
    "validation then FAILED. Given the failure log and the current file "
    "contents, produce the minimal set of source edits that make the build and "
    "tests pass. Rules: fix the source, never weaken or delete tests to force a "
    "pass; keep changes minimal and idiomatic for the language; do not introduce "
    "new external dependencies unless the failure is a missing dependency. "
    "Respond with ONLY a JSON object of the form "
    '{"fixes":[{"path":"<repo-relative path>","content":"<full new file '
    'content>"}],"notes":"<one-line summary>"}. Return each file you change in '
    "full (not a diff). If you cannot confidently fix it, return "
    '{"fixes":[],"notes":"..."}.'
)


def read_text(path: Path) -> Optional[str]:
    """Read a text file, returning None if it is unreadable or too large."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(errors="ignore")
    except OSError:
        return None


def iter_source_files(root: Path):
    """Yield candidate text source files under root, skipping vendor/build dirs."""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.suffix.lower() in _TEXT_EXTS or p.name in _BUILD_FILES:
            yield p


def gather_candidate_files(root: Path, log_text: str) -> list[tuple[str, str]]:
    """Pick the files most likely relevant to the failure.

    Priority: files whose relative path/name appears in the failure log, then
    build/config files, then remaining sources - capped by count and total
    size so the prompt stays bounded.
    """
    all_files = list(iter_source_files(root))
    rels = {p: str(p.relative_to(root)) for p in all_files}

    referenced, build_cfg, others = [], [], []
    for p in all_files:
        rel = rels[p]
        if rel in log_text or p.name in log_text:
            referenced.append(p)
        elif p.name in _BUILD_FILES:
            build_cfg.append(p)
        else:
            others.append(p)

    ordered = referenced + build_cfg + others
    selected: list[tuple[str, str]] = []
    total = 0
    for p in ordered:
        if len(selected) >= _MAX_FILES:
            break
        content = read_text(p)
        if content is None:
            continue
        if total + len(content) > _MAX_TOTAL_PROMPT:
            continue
        selected.append((rels[p], content))
        total += len(content)
    return selected


def build_user_message(log_text: str, files: list[tuple[str, str]]) -> str:
    """Assemble the user turn: failure log + the selected file contents."""
    parts = ["## Build/test failure log\n", log_text[-20_000:].strip() or "(no log captured)"]
    parts.append("\n\n## Current files\n")
    for rel, content in files:
        parts.append(f"\n### FILE: {rel}\n```\n{content}\n```\n")
    parts.append(
        "\n\nReturn ONLY the JSON object described in the system instructions."
    )
    return "".join(parts)


def parse_fixes(response_text: str) -> list[dict]:
    """Extract the fixes list from the model response (tolerates code fences)."""
    text = (response_text or "").strip()
    if "```" in text:
        # Strip a ```json ... ``` fence if present.
        start = text.find("```")
        fence = text[start + 3:]
        if fence.lower().startswith("json"):
            fence = fence[4:]
        end = fence.rfind("```")
        if end != -1:
            text = fence[:end].strip()
    # Fall back to the outermost JSON object.
    if not text.startswith("{"):
        lo, hi = text.find("{"), text.rfind("}")
        if lo != -1 and hi != -1 and hi > lo:
            text = text[lo:hi + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    fixes = data.get("fixes") if isinstance(data, dict) else None
    if not isinstance(fixes, list):
        return []
    out = []
    for f in fixes:
        if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("content"), str):
            out.append({"path": f["path"], "content": f["content"]})
    return out


def _safe_target(root: Path, rel_path: str) -> Optional[Path]:
    """Resolve rel_path under root, rejecting absolute paths and traversal."""
    rel = rel_path.strip().lstrip("/")
    if not rel:
        return None
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None  # escapes the working tree
    return candidate


def apply_fixes(root: Path, fixes: list[dict]) -> list[str]:
    """Write each fix to disk within root; return the list of applied paths."""
    applied: list[str] = []
    for fix in fixes:
        target = _safe_target(root, fix["path"])
        if target is None:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fix["content"])
            applied.append(str(target.relative_to(root.resolve())))
        except OSError:
            continue
    return applied


def invoke_bedrock(model_id: str, region: str, system_prompt: str, user_message: str) -> str:
    """Call Bedrock's Anthropic messages API and return the text response."""
    import boto3  # imported lazily so unit tests need no AWS SDK at import time

    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": _MAX_TOKENS,
        "temperature": 0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }
    resp = client.invoke_model(modelId=model_id, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    blocks = payload.get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def remediate(root: Path, log_text: str, model_id: str, region: str, invoker=invoke_bedrock) -> tuple[list[str], str]:
    """Core remediation: gather -> ask model -> apply. Returns (applied, notes).

    ``invoker`` is injectable so tests can supply a fake model response.
    """
    files = gather_candidate_files(root, log_text)
    if not files:
        return [], "no source files found to remediate"
    user_message = build_user_message(log_text, files)
    response = invoker(model_id, region, _SYSTEM_PROMPT, user_message)
    fixes = parse_fixes(response)
    if not fixes:
        return [], "model returned no applicable fix"
    applied = apply_fixes(root, fixes)
    return applied, "applied %d file fix(es)" % len(applied)


def main() -> int:  # pragma: no cover - CLI glue exercised in the buildspec
    root = Path(os.environ.get("REMEDIATE_ROOT", ".")).resolve()
    model_id = os.environ.get("BEDROCK_MODEL_ID", "")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    log_text = ""
    log_path = Path("validation.log")
    if log_path.exists():
        log_text = log_path.read_text(errors="ignore")

    if not model_id:
        print("remediate: BEDROCK_MODEL_ID not set - cannot remediate", file=sys.stderr)
        return 2

    try:
        applied, notes = remediate(root, log_text, model_id, region)
    except Exception as exc:  # noqa: BLE001 - any failure -> escalate
        print(f"remediate: error during remediation: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"applied": applied, "notes": notes}))
    if not applied:
        return 3  # nothing fixed -> escalate
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
