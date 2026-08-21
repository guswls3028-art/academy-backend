"""Cross-domain assessment-correction dependencies for result read models."""

from apps.domains.progress.models import AssessmentCorrection


def set_teacher_assessment_resolution(**kwargs):
    from apps.domains.progress.dispatcher import (
        set_teacher_assessment_resolution as set_resolution,
    )

    return set_resolution(**kwargs)


__all__ = ["AssessmentCorrection", "set_teacher_assessment_resolution"]
