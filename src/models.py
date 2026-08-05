"""Shared enums for the Modernization Factory web app.

`AppStatus` is the run lifecycle status persisted in DynamoDB and surfaced to
the browser; `UpgradeType` is retained on the run record for backward
compatibility with the change report.
"""

from enum import Enum


class AppStatus(str, Enum):
    QUEUED = "QUEUED"
    ASSESSING = "ASSESSING"
    TRANSFORMING = "TRANSFORMING"
    BUILDING = "BUILDING"
    REMEDIATING = "REMEDIATING"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class UpgradeType(str, Enum):
    PYTHON2_TO_3 = "python2_to_3"
    DJANGO_UPGRADE = "django_upgrade"
    FLASK_UPGRADE = "flask_upgrade"
    DEPENDENCY_MODERNIZATION = "dependency_modernization"
    RUNTIME_UPGRADE = "runtime_upgrade"
