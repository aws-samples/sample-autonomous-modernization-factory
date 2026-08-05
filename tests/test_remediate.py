"""Tests for src/remediate.py - the AI auto-remediation agent.

Bedrock is never called: the model response is injected via the ``invoker``
seam, so these tests are hermetic (no AWS, no network).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import remediate


# --------------------------------------------------------------------------- #
# parse_fixes
# --------------------------------------------------------------------------- #
def test_parse_fixes_plain_json():
    text = '{"fixes":[{"path":"a.py","content":"print(1)"}],"notes":"ok"}'
    fixes = remediate.parse_fixes(text)
    assert fixes == [{"path": "a.py", "content": "print(1)"}]


def test_parse_fixes_with_code_fence():
    text = "Here you go:\n```json\n{\"fixes\":[{\"path\":\"x/y.py\",\"content\":\"x=1\"}]}\n```\nthanks"
    fixes = remediate.parse_fixes(text)
    assert fixes == [{"path": "x/y.py", "content": "x=1"}]


def test_parse_fixes_empty_or_malformed():
    assert remediate.parse_fixes("") == []
    assert remediate.parse_fixes("not json at all") == []
    assert remediate.parse_fixes('{"fixes": "not-a-list"}') == []
    # entries missing required keys are dropped
    assert remediate.parse_fixes('{"fixes":[{"path":"a"}]}') == []


# --------------------------------------------------------------------------- #
# path safety + apply
# --------------------------------------------------------------------------- #
def test_safe_target_rejects_traversal_and_contains_absolute(tmp_path):
    root = tmp_path.resolve()
    # Traversal that escapes the root is rejected outright.
    assert remediate._safe_target(tmp_path, "../evil.py") is None
    # An absolute path is neutralized to stay *within* the sandbox root (safe).
    abs_target = remediate._safe_target(tmp_path, "/etc/passwd")
    assert abs_target is not None
    assert str(abs_target).startswith(str(root))
    assert abs_target == root / "etc" / "passwd"
    # A normal relative path resolves inside the root.
    ok = remediate._safe_target(tmp_path, "pkg/mod.py")
    assert ok is not None and str(ok).startswith(str(root))


def test_apply_fixes_writes_only_safe_paths(tmp_path):
    fixes = [
        {"path": "app.py", "content": "print('fixed')\n"},
        {"path": "sub/dir/util.py", "content": "X = 1\n"},
        {"path": "../escape.py", "content": "nope"},
    ]
    applied = remediate.apply_fixes(tmp_path, fixes)
    assert set(applied) == {"app.py", "sub/dir/util.py"}
    assert (tmp_path / "app.py").read_text() == "print('fixed')\n"
    assert (tmp_path / "sub/dir/util.py").read_text() == "X = 1\n"
    assert not (tmp_path.parent / "escape.py").exists()


# --------------------------------------------------------------------------- #
# gather_candidate_files
# --------------------------------------------------------------------------- #
def test_gather_prioritizes_log_referenced_and_skips_vendor(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    log = "Traceback ... File \"app.py\", line 3\nModuleNotFoundError"
    files = remediate.gather_candidate_files(tmp_path, log)
    rels = [rel for rel, _ in files]
    assert "app.py" in rels           # referenced in the log
    assert "requirements.txt" in rels  # build/config file
    assert all("node_modules" not in r for r in rels)  # vendor dir skipped
    # log-referenced file is prioritized first
    assert rels[0] == "app.py"


# --------------------------------------------------------------------------- #
# remediate() end-to-end with an injected model
# --------------------------------------------------------------------------- #
def test_remediate_applies_model_fixes(tmp_path):
    (tmp_path / "broken.py").write_text("print 'hi'\n")  # py2 syntax

    def fake_invoker(model_id, region, system, user):
        assert "broken.py" in user  # the failing file is in the prompt
        return '{"fixes":[{"path":"broken.py","content":"print(\'hi\')\\n"}],"notes":"fixed print"}'

    applied, notes = remediate.remediate(
        tmp_path, "SyntaxError in broken.py", "model-x", "us-east-1", invoker=fake_invoker
    )
    assert applied == ["broken.py"]
    assert (tmp_path / "broken.py").read_text() == "print('hi')\n"


def test_remediate_no_fix_returns_empty(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")

    def fake_invoker(*_):
        return '{"fixes":[],"notes":"cannot fix"}'

    applied, notes = remediate.remediate(
        tmp_path, "some failure", "model-x", "us-east-1", invoker=fake_invoker
    )
    assert applied == []
    assert "no applicable fix" in notes


def test_remediate_no_source_files(tmp_path):
    applied, notes = remediate.remediate(
        tmp_path, "failure", "model-x", "us-east-1", invoker=lambda *a: "{}"
    )
    assert applied == []
    assert "no source files" in notes
