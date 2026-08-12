"""Public cross-domain entry points owned by the tools domain.

Imports stay lazy because Problem Studio loads document and Django integrations
that should not become startup dependencies of every consumer.
"""

from __future__ import annotations

from typing import Any


def beta_run_id_from_job_payload(payload: Any) -> str:
    from .problem_studio.beta_access import beta_run_id_from_job_payload as _impl

    return _impl(payload)


def settle_beta_run(*args: Any, **kwargs: Any):
    from .problem_studio.beta_access import settle_beta_run as _impl

    return _impl(*args, **kwargs)


def settle_explanation_step_failure(*args: Any, **kwargs: Any):
    from .problem_studio.explanation_workflow import settle_explanation_step_failure as _impl

    return _impl(*args, **kwargs)


def build_hwpx_editable_wrong_note_document(*args: Any, **kwargs: Any):
    from .problem_studio.hwpx_writer import build_hwpx_editable_wrong_note_document as _impl

    return _impl(*args, **kwargs)


def problem_review_report_fingerprint(*args: Any, **kwargs: Any):
    from .problem_review.readiness import report_fingerprint as _impl

    return _impl(*args, **kwargs)


def render_problem_review_report(*args: Any, **kwargs: Any):
    from .problem_review.renderers import render_problem_review_report as _impl

    return _impl(*args, **kwargs)
