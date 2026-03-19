# apps/domains/ai/callbacks.py
"""
AI Job 완료 후 도메인별 후속 처리를 담당한다.

핵심 규칙:
- AI Job의 상태(DONE/FAILED)는 이미 UoW에서 처리된 상태로 진입한다.
- 이 모듈은 AI 결과를 "도메인 엔티티에 반영"하는 역할만 한다.
- 멱등성 보장: 동일 job에 대해 중복 호출해도 안전해야 한다.
- callback 실패가 AI Job 상태를 되돌리지 않는다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def dispatch_ai_result_to_domain(
    *,
    job_id: str,
    status: str,
    result_payload: Optional[Dict[str, Any]],
    error: Optional[str],
    source_domain: Optional[str],
    source_id: Optional[str],
    tier: str = "basic",
) -> None:
    """
    AI Job 완료 후 도메인별 후속 처리 디스패처.
    source_domain에 따라 적절한 도메인 핸들러로 라우팅한다.
    """
    if source_domain != "submissions":
        logger.debug(
            "AI_CALLBACK_SKIP | source_domain=%s job_id=%s (not submissions)",
            source_domain, job_id,
        )
        return

    if not source_id:
        logger.warning(
            "AI_CALLBACK_SKIP | source_id empty | job_id=%s",
            job_id,
        )
        return

    t0 = time.monotonic()
    logger.info(
        "AI_CALLBACK_START | job_id=%s | submission_id=%s | status=%s | tier=%s",
        job_id, source_id, status, tier,
    )

    try:
        _handle_submission_ai_result(
            job_id=job_id,
            submission_id=int(source_id),
            status=status,
            result_payload=result_payload or {},
            error=error,
            tier=tier,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "AI_CALLBACK_SUCCESS | job_id=%s | submission_id=%s | elapsed_ms=%d",
            job_id, source_id, elapsed_ms,
        )
    except Exception:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "AI_CALLBACK_FAILED | job_id=%s | submission_id=%s | elapsed_ms=%d",
            job_id, source_id, elapsed_ms,
        )


def _handle_submission_ai_result(
    *,
    job_id: str,
    submission_id: int,
    status: str,
    result_payload: Dict[str, Any],
    error: Optional[str],
    tier: str,
) -> None:
    """
    Submission 도메인의 AI 결과 처리.

    1. AI 결과를 Submission에 반영 (상태 전이: DISPATCHED → ANSWERS_READY/NEEDS_ID/FAILED)
    2. ANSWERS_READY가 되면 채점 파이프라인 실행
    """
    from apps.domains.submissions.services.ai_omr_result_mapper import apply_ai_result
    from apps.domains.results.tasks.grading_tasks import grade_submission_task

    effective_status = status
    effective_error = error

    # Lite/Basic tier는 FAILED를 DONE으로 간주 (워커의 FAILED는 리소스 부족 등이므로)
    if status == "FAILED" and tier in ("lite", "basic"):
        effective_status = "DONE"
        effective_error = None
        logger.info(
            "AI_CALLBACK_TIER_OVERRIDE | tier=%s | FAILED→DONE | submission_id=%s",
            tier, submission_id,
        )

    # apply_ai_result는 payload에서 submission_id를 꺼냄
    payload = dict(result_payload)
    payload["submission_id"] = submission_id
    payload["status"] = effective_status
    payload["error"] = effective_error

    returned_id = apply_ai_result(payload)

    if not returned_id:
        logger.warning(
            "AI_CALLBACK_APPLY_NULL | submission_id=%s | job_id=%s",
            submission_id, job_id,
        )
        return

    # ANSWERS_READY가 된 경우에만 채점 실행
    from apps.domains.submissions.models import Submission
    try:
        sub_status = Submission.objects.filter(pk=returned_id).values_list("status", flat=True).first()
        if sub_status == Submission.Status.ANSWERS_READY:
            grade_submission_task(int(returned_id))
            logger.info(
                "AI_CALLBACK_GRADING_TRIGGERED | submission_id=%s | job_id=%s",
                returned_id, job_id,
            )
        else:
            logger.info(
                "AI_CALLBACK_GRADING_SKIPPED | submission_id=%s | status=%s | job_id=%s",
                returned_id, sub_status, job_id,
            )
    except Exception:
        logger.exception(
            "AI_CALLBACK_GRADING_ERROR | submission_id=%s | job_id=%s",
            returned_id, job_id,
        )


def detect_stuck_dispatched() -> list[dict]:
    """
    AIJob이 완료되었는데 Submission이 아직 DISPATCHED인 건을 감지한다.
    운영 모니터링/reconcile 전 진단용.
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.domains.submissions.models import Submission
    from apps.domains.ai.models import AIJobModel

    cutoff = timezone.now() - timedelta(minutes=30)
    stuck = Submission.objects.filter(
        status=Submission.Status.DISPATCHED,
        updated_at__lt=cutoff,
    ).values_list("id", flat=True)

    results = []
    for sub_id in stuck[:100]:
        ai_job = (
            AIJobModel.objects
            .filter(source_domain="submissions", source_id=str(sub_id))
            .order_by("-created_at")
            .first()
        )
        results.append({
            "submission_id": sub_id,
            "ai_job_id": ai_job.job_id if ai_job else None,
            "ai_job_status": ai_job.status if ai_job else None,
            "stuck": ai_job and ai_job.status in ("DONE", "FAILED", "REJECTED_BAD_INPUT"),
        })

    stuck_count = sum(1 for r in results if r["stuck"])
    if stuck_count > 0:
        logger.error(
            "AI_STUCK_DISPATCHED_DETECTED | count=%d | submissions=%s",
            stuck_count,
            [r["submission_id"] for r in results if r["stuck"]],
        )

    return results
