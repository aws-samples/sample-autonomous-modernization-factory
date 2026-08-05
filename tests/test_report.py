"""Tests for src/report.py - change-report builder (Task 7.2, 7.3).

Covers Requirements 12.2, 12.4, 11.9, 11.12 and design Property 6
(baseline fidelity - using a real git repo).

Run with: pytest tests/test_report.py -v
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.report import (
    build_change_report,
    determine_status,
    parse_name_status,
)


# --- parse_name_status (Req 12.2) ---
def test_parse_name_status_classifies():
    text = "A\tnew.py\nM\tapp.py\nD\told.py\n"
    added, modified, deleted = parse_name_status(text)
    assert added == ["new.py"]
    assert modified == ["app.py"]
    assert deleted == ["old.py"]


def test_parse_name_status_rename_and_copy():
    text = "R100\told.py\tnew.py\nC075\tsrc.py\tcopy.py\n"
    added, modified, deleted = parse_name_status(text)
    assert "new.py" in added and "old.py" in deleted   # rename
    assert "copy.py" in added                           # copy


def test_parse_name_status_empty():
    assert parse_name_status("") == ([], [], [])


# --- determine_status (Req 11.9, 11.12) ---
def test_status_completed():
    status, esc, err = determine_status(0, "Transformation complete. 3 files updated.", True)
    assert status == "COMPLETED" and esc is None and err is None


def test_status_failed_on_nonzero_exit():
    status, esc, err = determine_status(1, "boom: build failed", True)
    assert status == "FAILED"
    assert err and "boom" in err


def test_status_escalated_on_marker():
    status, esc, err = determine_status(0, "This codebase requires human review.", False)
    assert status == "ESCALATED"
    assert esc and "human review" in esc.lower()


def test_reason_truncated_to_1000():
    status, esc, err = determine_status(1, "x" * 5000, True)
    assert status == "FAILED" and len(err) == 1000


# --- build_change_report summary ---
def test_build_change_report_summary():
    r = build_change_report("run-1", "A\ta.py\nM\tb.py\n", "diff...", "report...")
    assert r["run_id"] == "run-1"
    assert r["files_added"] == ["a.py"]
    assert r["files_modified"] == ["b.py"]
    assert "1 file(s) added" in r["summary"]


# --- Property 6: baseline fidelity against a real git diff ---
def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,  # nosec B603 - fixed git args, test-only
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_baseline_fidelity_property(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("x = 1\n")
    (repo / "mod.py").write_text("old\n")
    (repo / "gone.py").write_text("bye\n")

    _git(["init"], repo)
    _git(["config", "user.email", "t@t.local"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "baseline"], repo)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,  # nosec B603 - fixed git args, test-only
                          capture_output=True, text=True).stdout.strip()

    # apply changes: add, modify, delete
    (repo / "added.py").write_text("new\n")
    (repo / "mod.py").write_text("new content\n")
    (repo / "gone.py").unlink()
    _git(["add", "-A"], repo)

    name_status = subprocess.run(  # nosec B603 - fixed git args, test-only
        ["git", "diff", "--name-status", base],
        cwd=repo, capture_output=True, text=True,
    ).stdout

    added, modified, deleted = parse_name_status(name_status)
    assert set(added) == {"added.py"}
    assert set(modified) == {"mod.py"}
    assert set(deleted) == {"gone.py"}
