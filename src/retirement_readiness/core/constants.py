"""Stable runtime constants for the current retirement assessment."""

from __future__ import annotations

RETIREMENT_SKILL_ID = "retirement_readiness_assessment"
SURVEY_ID = "survey_0909_household_v2.0.1"
CALCULATION_ID = "calc_tool_0.10.2"

SLOT_CURRENT = "current"
SLOT_DRAFT = "draft"
ASSESSMENT_SLOTS = {SLOT_CURRENT, SLOT_DRAFT}

STATUS_DRAFT = "draft"
STATUS_REPORT_QUEUED = "report_queued"
STATUS_REPORT_GENERATING = "report_generating"
STATUS_REPORT_READY = "report_ready"
STATUS_REPORT_FAILED = "report_failed"

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_READY = "ready"
JOB_FAILED = "failed"
