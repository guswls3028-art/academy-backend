# PATH: apps/domains/submissions/signals.py
"""
Exam/Homework 삭제 시 관련 active submission auto-discard.

배경:
- submission.target_id 가 deleted Exam/Homework 를 가리키는 dangling row 가
  운영 인박스에 남아 운영자가 처리할 동선이 없는 데드락을 만든다.
- Submission 자체에는 FK constraint 없음 (target_type 으로 다형 참조).
- DB cascade 가 작동하지 않으므로 application-level signal 로 처리.

정책:
- DONE submission 은 historical 데이터로 보존 (성적 이력).
- pending/needs_id/answers_ready/grading 은 자동 폐기 (FAILED + meta.discarded).
- 사유: cascade_target_deleted (운영자 식별 가능).

Tenant isolation: target.tenant 필터로 한정.
"""
from __future__ import annotations

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone


def _cascade_discard(target_type: str, target_id: int, tenant_id: int, reason: str) -> int:
    """
    target_type/target_id 에 매칭되는 active submission 을 FAILED + discarded 처리.
    - DONE / SUPERSEDED 는 historical 보존 (제외).
    - 이미 FAILED 면 meta.discarded 만 보강.
    Returns: 처리된 submission 개수.
    """
    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        CASCADE_DISCARD_STATUSES,
        fail_submission,
    )

    qs = Submission.objects.filter(
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=int(target_id),
        status__in=CASCADE_DISCARD_STATUSES,
    )

    count = 0
    now = timezone.now().isoformat()
    for s in qs:
        meta = dict(s.meta or {})
        meta["discarded"] = {
            "at": now,
            "by_user_id": None,  # system
            "reason": reason,
        }
        meta.setdefault("manual_review", {})
        meta["manual_review"]["required"] = False
        meta["manual_review"]["resolved_at"] = now

        s.meta = meta
        if s.status != Submission.Status.FAILED:
            fail_submission(
                s,
                error_message=f"discarded:{reason}",
                actor="system.cascade_discard",
                extra_update_fields=["meta"],
            )
        else:
            s.save(update_fields=["meta", "updated_at"])
        count += 1
    return count


@receiver(pre_delete, sender="exams.Exam")
def _on_exam_pre_delete(sender, instance, **_kwargs):
    if not instance.pk:
        return
    _cascade_discard(
        target_type="exam",
        target_id=instance.pk,
        tenant_id=instance.tenant_id,
        reason="cascade_exam_deleted",
    )


@receiver(pre_delete, sender="homework_results.Homework")
def _on_homework_pre_delete(sender, instance, **_kwargs):
    if not instance.pk:
        return
    _cascade_discard(
        target_type="homework",
        target_id=instance.pk,
        tenant_id=instance.tenant_id,
        reason="cascade_homework_deleted",
    )
