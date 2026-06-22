"""Cross-domain submission failure helpers.

Domain tasks/adapters use this support boundary when they need to fail a
submission without importing submission internals directly.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def mark_submission_failed(
    submission_id: int,
    *,
    tenant_id: int | None = None,
    error_message: str,
    actor: str,
    meta_patch: Mapping[str, Any] | None = None,
) -> bool:
    try:
        resolved_submission_id = int(submission_id)
        resolved_tenant_id = int(tenant_id) if tenant_id is not None else None
    except (TypeError, ValueError):
        logger.warning(
            "submission failure skipped: invalid identifiers submission_id=%s tenant_id=%s actor=%s",
            submission_id,
            tenant_id,
            actor,
        )
        return False

    if resolved_submission_id <= 0 or (resolved_tenant_id is not None and resolved_tenant_id <= 0):
        return False

    from django.db import transaction

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        InvalidTransitionError,
        can_fail_submission,
        fail_submission,
    )

    filters: dict[str, int] = {"id": resolved_submission_id}
    if resolved_tenant_id is not None:
        filters["tenant_id"] = resolved_tenant_id

    with transaction.atomic():
        submission = (
            Submission.objects
            .select_for_update()
            .filter(**filters)
            .first()
        )
        if not submission or submission.status == Submission.Status.FAILED:
            return False
        if not can_fail_submission(submission.status):
            logger.warning(
                "submission failure transition skipped: submission_id=%s tenant_id=%s status=%s actor=%s",
                resolved_submission_id,
                resolved_tenant_id,
                getattr(submission, "status", None),
                actor,
            )
            return False

        extra_update_fields: list[str] = []
        if meta_patch:
            meta = dict(submission.meta or {})
            meta.update(meta_patch)
            submission.meta = meta
            extra_update_fields.append("meta")

        try:
            fail_submission(
                submission,
                error_message=error_message,
                actor=actor,
                extra_update_fields=extra_update_fields,
            )
        except InvalidTransitionError:
            logger.warning(
                "submission failure transition failed: submission_id=%s tenant_id=%s status=%s actor=%s",
                resolved_submission_id,
                resolved_tenant_id,
                getattr(submission, "status", None),
                actor,
            )
            return False
        return True
