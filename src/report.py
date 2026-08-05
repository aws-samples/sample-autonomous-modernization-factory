"""Change-report builder (Requirement 12).

Runs inside the CodeBuild transformer after the ATX CLI. Derives the change
report from `git diff` against the Baseline_Commit plus the ATX CLI report,
and determines the terminal run status.

Pure functions here are unit-tested; `main()` is the thin CLI the buildspec
invokes to read the git output / atx report files and write:
  - change_report.json  (ChangeReport)
  - change_report.md    (human-readable)
  - result.json         (terminal status + reason + artifact/report keys)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

_MAX_PATCH = 200_000
_MAX_REPORT = 100_000
_MAX_REASON = 1000

# Markers in the ATX report that indicate a run should be escalated (Req 11.12).
_ESCALATE_MARKERS = (
    "cannot transform",
    "cannot be transformed",
    "requires human",
    "human review",
    "manual review",
    "unable to transform",
    "not supported",
)


def parse_name_status(text: str) -> tuple[list[str], list[str], list[str]]:
    """Parse `git diff --name-status <base>` into (added, modified, deleted)."""
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0].strip()
        if not code:
            continue
        letter = code[0].upper()
        if letter == "A" and len(parts) >= 2:
            added.append(parts[1])
        elif letter == "M" and len(parts) >= 2:
            modified.append(parts[1])
        elif letter == "D" and len(parts) >= 2:
            deleted.append(parts[1])
        elif letter == "R" and len(parts) >= 3:  # rename: old -> new
            deleted.append(parts[1])
            added.append(parts[2])
        elif letter == "C" and len(parts) >= 3:  # copy: src -> new
            added.append(parts[2])
        elif len(parts) >= 2:  # T (type change) and others -> modified
            modified.append(parts[1])
    return added, modified, deleted


def determine_status(
    atx_exit_code: int,
    atx_report: str,
    has_changes: bool,
) -> tuple[str, Optional[str], Optional[str]]:
    """Return (status, escalation_reason, error_message).

    - non-zero exit -> FAILED (Req 11.9)
    - escalation marker in report -> ESCALATED (Req 11.12)
    - otherwise -> COMPLETED
    """
    text = (atx_report or "").lower()
    if atx_exit_code != 0:
        reason = (atx_report or "ATX CLI returned a non-zero exit code").strip()
        return "FAILED", None, reason[:_MAX_REASON]
    if any(marker in text for marker in _ESCALATE_MARKERS):
        reason = (atx_report or "codebase requires human review").strip()
        return "ESCALATED", reason[:_MAX_REASON], None
    return "COMPLETED", None, None


def build_change_report(
    run_id: str,
    name_status_text: str,
    full_patch: str,
    atx_report: str,
) -> dict:
    """Assemble the ChangeReport dict (Req 12.1–12.4)."""
    added, modified, deleted = parse_name_status(name_status_text)
    summary = (
        f"{len(added)} file(s) added, {len(modified)} modified, "
        f"{len(deleted)} deleted."
    )
    return {
        "run_id": run_id,
        "files_added": added,
        "files_modified": modified,
        "files_deleted": deleted,
        "summary": summary,
        "diff_text": (full_patch or "")[:_MAX_PATCH],
        "cli_report": (atx_report or "")[:_MAX_REPORT],
    }


def render_markdown(report: dict) -> str:
    """Render a ChangeReport dict as human-readable markdown (Req 12.8)."""
    lines = [f"# Transformation Change Report - {report['run_id']}", "", report["summary"], ""]

    def section(title: str, files: list[str]) -> None:
        lines.append(f"## {title} ({len(files)})")
        if files:
            lines.extend(f"- `{f}`" for f in files)
        else:
            lines.append("_none_")
        lines.append("")

    section("Added", report["files_added"])
    section("Modified", report["files_modified"])
    section("Deleted", report["files_deleted"])
    if report.get("cli_report"):
        lines += ["## ATX CLI Report", "", "```", report["cli_report"], "```", ""]
    return "\n".join(lines)


def _read(path: str) -> str:
    try:
        with open(path, "r", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def main() -> int:  # pragma: no cover - CLI glue exercised in the buildspec
    """CLI entrypoint for the CodeBuild buildspec.

    Env: RUN_ID, RESULTS_BUCKET. Reads diff_namestatus.txt, diff_full.patch,
    atx_report.txt, atx_exit.txt from the working directory; writes
    change_report.json, change_report.md, result.json.
    """
    run_id = os.environ.get("RUN_ID", "unknown")
    name_status = _read("diff_namestatus.txt")
    full_patch = _read("diff_full.patch")
    atx_report = _read("atx_report.txt")
    try:
        atx_exit = int((_read("atx_exit.txt") or "0").strip() or "0")
    except ValueError:
        atx_exit = 1

    report = build_change_report(run_id, name_status, full_patch, atx_report)
    has_changes = bool(report["files_added"] or report["files_modified"] or report["files_deleted"])
    status, escalation_reason, error_message = determine_status(atx_exit, atx_report, has_changes)

    with open("change_report.json", "w") as f:
        json.dump(report, f, indent=2)
    with open("change_report.md", "w") as f:
        f.write(render_markdown(report))

    result = {
        "status": status,
        "escalation_reason": escalation_reason,
        "error_message": error_message,
        "artifact_key": f"results/{run_id}/modernized.zip",
        "report_key": f"results/{run_id}/change_report.json",
    }
    with open("result.json", "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps({"run_id": run_id, "status": status, "summary": report["summary"]}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
