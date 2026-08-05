#!/usr/bin/env python3
"""Thin entrypoint for the CodeBuild transformer to build the change report.

The report logic lives in ``src/report.py`` (stdlib-only, fully unit-tested).
This launcher makes it runnable as ``python scripts/build_report.py`` from the
repo, and the transformer image ships ``report.py`` alongside so the buildspec
can invoke it in the sandbox. See Requirement 12 and the design buildspec.
"""

import os
import sys

# Allow running both from the repo root and when report.py is colocated.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from src.report import main  # repo layout
except ImportError:  # pragma: no cover - sandbox layout (report.py colocated)
    from report import main  # type: ignore

if __name__ == "__main__":
    sys.exit(main())
