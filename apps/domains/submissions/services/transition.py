# PATH: apps/domains/submissions/services/transition.py
"""
Submission 상태 전이 SSOT.

모든 Submission.status 변경은 이 모듈의 transit() 함수를 통해서만 이루어져야 한다.
Direct assignment (submission.status = ...) 금지.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from apps.domains.submissions.models import Submission

logger = logging.getLogger(__name__)

S = Submission.Status

# ──────────────────────────────────────────────
# 허용 전이 맵 (SSOT)
# ──────────────────────────────────────────────
STATUS_FLOW: dict[str, set[str]] = {
    S.SUBMITTED:            {S.DISPATCHED, S.ANSWERS_READY, S.GRADING, S.FAILED},
    S.DISPATCHED:           {S.ANSWERS_READY, S.NEEDS_IDENTIFICATION, S.FAILED},
    S.ANSWERS_READY:        {S.GRADING},
    S.GRADING:              {S.DONE, S.FAILED},
    S.FAILED:               {S.SUBMITTED},
    S.NEEDS_IDENTIFICATION: {S.ANSWERS_READY},
    S.DONE:                 {S.SUPERSEDED},
    S.SUPERSEDED:           set(),  # terminal
}

# 관리자 오버라이드: 수동 답안 편집 시 허용되는 추가 전이
# manual_edit에서만 사용. 일반 파이프라인에서는 불허.
ADMIN_OVERRIDE_FLOW: dict[str, set[str]] = {
    S.DONE:                 {S.ANSWERS_READY},
    S.FAILED:               {S.ANSWERS_READY},
    S.SUBMITTED:            {S.ANSWERS_READY},
    S.DISPATCHED:           {S.ANSWERS_READY},
    S.NEEDS_IDENTIFICATION: {S.ANSWERS_READY},  # 이미 STATUS_FLOW에도 있지만 명시
}

# 종단 상태: 이 상태에 도달하면 일반 전이 불가
TERMINAL_STATES: frozenset[str] = frozenset({S.DONE, S.SUPERSEDED})

# GRADING 중 전이 금지 (admin_override도 불허)
GRADING_LOCKED: frozenset[str] = frozenset({S.GRADING})


class InvalidTransitionError(Exception):
    """허용되지 않는 상태 전이."""

    def __init__(self, from_status: str, to_status: str, reason: str = ""):
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason or f"{from_status} → {to_status} is not allowed"
        super().__init__(self.reason)


def can_transit(
    from_status: str,
    to_status: str,
    *,
    admin_override: bool = False,
) -> bool:
    """전이 가능 여부 확인. admin_override=True이면 관리자 오버라이드 전이도 허용."""
    allowed = STATUS_FLOW.get(from_status, set())
    if to_status in allowed:
        return True
    if admin_override:
        override_allowed = ADMIN_OVERRIDE_FLOW.get(from_status, set())
        return to_status in override_allowed
    return False


def transit(
    submission: Submission,
    to_status: str,
    *,
    error_message: str = "",
    admin_override: bool = False,
    actor: str = "",
) -> None:
    """
    Submission 상태를 전이한다.

    - STATUS_FLOW 검증을 강제한다.
    - 종단 상태에서의 전이를 차단한다 (DONE→SUPERSEDED는 허용).
    - admin_override=True이면 ADMIN_OVERRIDE_FLOW에 정의된 추가 전이를 허용한다.
    - GRADING 상태에서는 admin_override도 차단한다.
    - 전이 실패 시 InvalidTransitionError를 발생시킨다.

    주의: 이 함수는 submission.save()를 호출하지 않는다.
          호출자가 save()를 직접 호출해야 한다.
          이는 호출자가 다른 필드도 함께 업데이트할 수 있도록 하기 위함이다.
    """
    from_status = submission.status

    # GRADING 중에는 admin_override도 차단 (DONE/FAILED만 허용 — STATUS_FLOW에 정의됨)
    if admin_override and from_status in GRADING_LOCKED:
        raise InvalidTransitionError(
            from_status, to_status,
            f"Cannot override: submission {submission.pk} is currently {from_status}",
        )

    if not can_transit(from_status, to_status, admin_override=admin_override):
        raise InvalidTransitionError(
            from_status, to_status,
            f"Submission {submission.pk}: {from_status} → {to_status}"
            f" (admin_override={admin_override})",
        )

    submission.status = to_status
    if error_message:
        submission.error_message = error_message
    elif to_status not in (S.FAILED,):
        # 실패가 아닌 전이 시 에러 메시지 초기화
        submission.error_message = ""

    logger.info(
        "Submission %s: %s → %s (actor=%s, override=%s)",
        submission.pk, from_status, to_status, actor or "system", admin_override,
    )


def transit_save(
    submission: Submission,
    to_status: str,
    *,
    error_message: str = "",
    admin_override: bool = False,
    actor: str = "",
    extra_update_fields: Optional[list[str]] = None,
) -> None:
    """transit() + save() 편의 함수."""
    transit(
        submission, to_status,
        error_message=error_message,
        admin_override=admin_override,
        actor=actor,
    )
    fields = ["status", "error_message", "updated_at"]
    if extra_update_fields:
        fields = list(set(fields + extra_update_fields))
    submission.save(update_fields=fields)


def bulk_transit(
    queryset,
    to_status: str,
    *,
    from_status: Optional[str] = None,
) -> int:
    """
    Queryset 기반 bulk 전이. from_status가 지정되면 해당 상태인 것만 전이.
    DONE → SUPERSEDED (retake) 용도.
    """
    if from_status and not can_transit(from_status, to_status):
        raise InvalidTransitionError(
            from_status, to_status,
            f"Bulk transit: {from_status} → {to_status} not allowed",
        )
    if from_status:
        queryset = queryset.filter(status=from_status)
    return queryset.update(status=to_status)
