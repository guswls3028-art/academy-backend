from __future__ import annotations

EVENT_SCREEN_VIEW = "screen_view"
EVENT_SCREEN_ENGAGED = "screen_engaged"
EVENT_CTA_IMPRESSION = "cta_impression"
EVENT_CTA_CLICK = "cta_click"
EVENT_TASK_START = "task_start"
EVENT_TASK_SUCCESS = "task_success"
EVENT_TASK_FAILURE = "task_failure"

EVENT_TYPES = (
    EVENT_SCREEN_VIEW,
    EVENT_SCREEN_ENGAGED,
    EVENT_CTA_IMPRESSION,
    EVENT_CTA_CLICK,
    EVENT_TASK_START,
    EVENT_TASK_SUCCESS,
    EVENT_TASK_FAILURE,
)

AUDIENCE_BY_ROLE = {
    "owner": "teacher_staff",
    "admin": "teacher_staff",
    "teacher": "teacher_staff",
    "staff": "teacher_staff",
    "parent": "parent",
    "student": "student",
}

SURFACES = ("admin", "teacher", "student")
DEVICE_CLASSES = ("mobile", "tablet", "desktop")
FAILURE_CATEGORIES = ("validation", "network", "permission", "server", "unknown")

MAX_BATCH_EVENTS = 20
MAX_BATCH_BYTES = 64 * 1024
SCHEMA_VERSION = 1
