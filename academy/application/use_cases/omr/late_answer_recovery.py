"""
Late OMR AI answer recovery.

교사가 학생을 수동 매칭하는 동안 AI worker 가 아직 처리 중이면 submission 이
DONE(0점) 으로 먼저 굳고, 뒤늦은 AI callback 은 멱등성 가드에 막힐 수 있다.
이 모듈은 그 상태(DONE/ANSWERS_READY + 답안 0개 + DONE AI 결과 존재)를 찾아
기존 mapper/grader 정본 경로로 다시 흘려보낸다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.db.models import Count
from django.utils import timezone

from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result
from academy.application.use_cases.omr.grading_readiness import (
    grade_omr_submission_if_ready,
)


logger = logging.getLogger(__name__)

RECOVERABLE_STATUSES: tuple[str, ...] = (
    Submission.Status.ANSWERS_READY,
    Submission.Status.DONE,
)


@dataclass(frozen=True)
class LateAIAnswerCandidate:
    """늦게 도착한 AI 답안을 자동 재적용할 수 있는 submission."""

    submission_id: int
    tenant_id: int
    status: str
    target_id: int
    enrollment_id: int
    ai_job_pk: int
    ai_job_id: str
    answers_count: int
    age_min: float


@dataclass
class LateAIAnswerRecoveryReport:
    """recover_late_ai_answers() 의 단일 호출 결과."""

    detected: list[LateAIAnswerCandidate] = field(default_factory=list)
    recovered: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)


def _extract_worker_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("result")
    if isinstance(nested, dict) and nested:
        return nested

    envelope_keys = {
        "submission_id",
        "job_id",
        "status",
        "error",
        "tenant_id",
        "kind",
        "received_at",
        "version",
        "result",
    }
    return {k: v for k, v in payload.items() if k not in envelope_keys}


def _answers_len(worker_result: dict[str, Any]) -> int:
    answers = worker_result.get("answers")
    return len(answers) if isinstance(answers, list) else 0


def _latest_done_ai_result_with_answers(
    submission_id: int,
) -> tuple[AIJobModel | None, dict[str, Any]]:
    jobs = (
        AIJobModel.objects.filter(
            source_domain="submissions",
            source_id=str(submission_id),
            status="DONE",
        )
        .order_by("-updated_at", "-id")[:5]
    )
    for job in jobs:
        result = AIResultModel.objects.filter(job=job).first()
        if result is None:
            continue
        worker_result = _extract_worker_result(result.payload)
        if _answers_len(worker_result) > 0:
            return job, worker_result
    return None, {}


def detect_late_ai_answer_candidates(
    *,
    lookback_days: int = 14,
    limit: int = 100,
) -> list[LateAIAnswerCandidate]:
    """
    DONE/ANSWERS_READY 이지만 답안이 비어 있고, 재적용 가능한 AI 결과가 있는 OMR.
    """
    cutoff = timezone.now() - timedelta(days=max(int(lookback_days), 1))
    qs = (
        Submission.objects.filter(
            source=Submission.Source.OMR_SCAN,
            target_type=Submission.TargetType.EXAM,
            status__in=RECOVERABLE_STATUSES,
            enrollment_id__isnull=False,
            updated_at__gte=cutoff,
        )
        .annotate(answer_count=Count("answers"))
        .filter(answer_count=0)
        .only("id", "tenant_id", "status", "target_id", "enrollment_id", "updated_at")
        .order_by("updated_at", "id")[: max(int(limit), 1)]
    )

    now = timezone.now()
    out: list[LateAIAnswerCandidate] = []
    for submission in qs:
        job, worker_result = _latest_done_ai_result_with_answers(int(submission.id))
        if job is None:
            continue
        age_min = (now - submission.updated_at).total_seconds() / 60.0
        out.append(
            LateAIAnswerCandidate(
                submission_id=int(submission.id),
                tenant_id=int(submission.tenant_id or 0),
                status=str(submission.status),
                target_id=int(submission.target_id or 0),
                enrollment_id=int(submission.enrollment_id or 0),
                ai_job_pk=int(job.id),
                ai_job_id=str(job.job_id),
                answers_count=_answers_len(worker_result),
                age_min=round(age_min, 1),
            )
        )
    return out


def _build_callback_payload(
    *,
    candidate: LateAIAnswerCandidate,
    job: AIJobModel,
    worker_result: dict[str, Any],
) -> dict[str, Any]:
    version = worker_result.get("version")
    if not version:
        answers = worker_result.get("answers")
        if isinstance(answers, list) and answers:
            first = answers[0]
            if isinstance(first, dict):
                version = first.get("version")

    payload: dict[str, Any] = {
        "submission_id": candidate.submission_id,
        "job_id": job.job_id,
        "tenant_id": str(candidate.tenant_id),
        "status": "DONE",
        "error": job.error_message or None,
        "kind": "omr_scan",
        "result": worker_result,
    }
    if version:
        payload["version"] = version
    return payload


def _mark_recovered(
    *,
    submission: Submission,
    candidate: LateAIAnswerCandidate,
    actor: str,
) -> None:
    meta = dict(submission.meta or {})
    meta["late_ai_answer_recovery"] = {
        "at": timezone.now().isoformat(),
        "actor": actor,
        "ai_job_id": candidate.ai_job_id,
        "answers_count": candidate.answers_count,
        "from_status": candidate.status,
    }
    submission.meta = meta
    submission.save(update_fields=["meta", "updated_at"])


def recover_late_ai_answers(
    *,
    actor: str = "late_ai_answer_recovery",
    dry_run: bool = False,
    lookback_days: int = 14,
    limit: int = 100,
) -> LateAIAnswerRecoveryReport:
    """
    늦게 도착한 AI 답안을 mapper/grader 정본 경로로 재적용한다.

    재진입 안전:
    - 답안이 이미 생겼으면 skip.
    - AI 결과가 사라졌거나 답안이 없으면 skip.
    - mapper 가 tenant_id 를 다시 교차검증한다.
    """
    detected = detect_late_ai_answer_candidates(
        lookback_days=lookback_days,
        limit=limit,
    )
    report = LateAIAnswerRecoveryReport(detected=detected)
    if not detected:
        return report

    if dry_run:
        for candidate in detected:
            logger.warning(
                "OMR_LATE_AI_ANSWER_RECOVERY_DRYRUN | sub=%s | status=%s | "
                "answers=%s | job=%s | tenant=%s",
                candidate.submission_id,
                candidate.status,
                candidate.answers_count,
                candidate.ai_job_id,
                candidate.tenant_id,
            )
        return report

    for candidate in detected:
        try:
            if SubmissionAnswer.objects.filter(
                submission_id=candidate.submission_id
            ).exists():
                report.skipped.append((candidate.submission_id, "answers_exist"))
                continue

            job, worker_result = _latest_done_ai_result_with_answers(
                candidate.submission_id
            )
            if job is None or _answers_len(worker_result) == 0:
                report.skipped.append((candidate.submission_id, "ai_answers_missing"))
                continue

            apply_omr_ai_result(
                _build_callback_payload(
                    candidate=candidate,
                    job=job,
                    worker_result=worker_result,
                )
            )

            submission = Submission.objects.get(id=candidate.submission_id)
            answer_count = SubmissionAnswer.objects.filter(
                submission_id=candidate.submission_id
            ).count()
            if answer_count == 0:
                report.skipped.append((candidate.submission_id, "no_answers_written"))
                continue

            grade_omr_submission_if_ready(
                candidate.submission_id,
                actor="omr.late_answer_recovery",
            )
            submission.refresh_from_db()

            _mark_recovered(submission=submission, candidate=candidate, actor=actor)
            report.recovered.append(candidate.submission_id)
            logger.error(
                "OMR_LATE_AI_ANSWER_RECOVERY | sub=%s | answers=%s | "
                "job=%s | tenant=%s | status=%s",
                candidate.submission_id,
                answer_count,
                job.job_id,
                candidate.tenant_id,
                submission.status,
            )
        except Exception as exc:
            logger.exception(
                "OMR_LATE_AI_ANSWER_RECOVERY_FAILED | sub=%s",
                candidate.submission_id,
            )
            report.failed.append((candidate.submission_id, str(exc)[:500]))

    return report
