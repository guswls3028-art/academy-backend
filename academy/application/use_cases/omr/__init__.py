"""OMR application use cases."""

from academy.application.use_cases.omr.grading_readiness import (
    OMRGradeDecision,
    grade_omr_submission_if_ready,
)

__all__ = [
    "OMRGradeDecision",
    "grade_omr_submission_if_ready",
]
