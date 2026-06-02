# apps/domains/results/services/attempt_service.py
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.domains.results.models import ExamAttempt
from apps.domains.exams.models import Exam

logger = logging.getLogger(__name__)

_ATTACHABLE_MANUAL_SOURCES = {
    "admin_manual_total",
    "admin_manual_objective",
    "admin_manual_subjective",
}


class ExamAttemptService:
    """
    ExamAttempt 생성/관리 전담

    🔥 동시성 보장:
    - submission_id 중복: DB unique constraint (unique_submission_per_attempt) + select_for_update
    - is_representative 유일성: DB unique constraint (unique_representative_per_exam_enrollment)
    - attempt_index 순서: (exam, enrollment) row-level lock으로 직렬화
    """

    @staticmethod
    @transaction.atomic
    def create_for_submission(
        *,
        exam_id: int,
        enrollment_id: int,
        submission_id: int,
    ) -> ExamAttempt:

        # -------------------------------------------------
        # 1️⃣ Exam 정책 로딩
        # -------------------------------------------------
        exam = Exam.objects.filter(id=int(exam_id)).first()
        allow_retake = bool(getattr(exam, "allow_retake", False)) if exam else False
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1) if exam else 1

        # -------------------------------------------------
        # 2️⃣ open_at / close_at 정책 강제
        # -------------------------------------------------
        if exam:
            now = timezone.now()
            open_at = getattr(exam, "open_at", None)
            close_at = getattr(exam, "close_at", None)

            if open_at and now < open_at:
                raise ValidationError("Exam not open yet.")
            if close_at and now > close_at:
                raise ValidationError("Exam is closed.")

        # -------------------------------------------------
        # 3️⃣ 동시성 안전: (exam, enrollment) lock + submission 중복 체크
        # -------------------------------------------------
        qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
        )

        # submission_id 중복 체크: lock 내부에서 수행 (race-free)
        if qs.filter(submission_id=int(submission_id)).exists():
            raise ValidationError(
                f"Attempt already exists for submission {submission_id}."
            )

        last = qs.aggregate(Max("attempt_index")).get("attempt_index__max") or 0
        next_index = int(last) + 1

        # -------------------------------------------------
        # 4️⃣ 정책 강제
        # -------------------------------------------------
        if not allow_retake and next_index > 1:
            raise ValidationError("Retake is not allowed for this exam.")

        if allow_retake and next_index > max_attempts:
            raise ValidationError("Max attempts exceeded.")

        # -------------------------------------------------
        # 5️⃣ 대표 attempt 교체 (원자적: lock 범위 내)
        # -------------------------------------------------
        qs.filter(is_representative=True).update(is_representative=False)

        try:
            attempt = ExamAttempt.objects.create(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                submission_id=submission_id,
                attempt_index=next_index,
                is_retake=(last > 0),
                is_representative=True,
                status="pending",
            )
        except IntegrityError as e:
            # DB constraint가 잡은 경우 (unique_submission_per_attempt 등)
            logger.warning(
                "ExamAttempt create IntegrityError: exam=%s enrollment=%s submission=%s — %s",
                exam_id, enrollment_id, submission_id, e,
            )
            raise ValidationError(
                f"Duplicate attempt for submission {submission_id} (caught by DB constraint)."
            ) from e

        return attempt

    @staticmethod
    @transaction.atomic
    def attach_manual_score_placeholder_for_submission(
        *,
        exam_id: int,
        enrollment_id: int,
        submission_id: int,
    ) -> ExamAttempt | None:
        """
        Attach a real submission to a pre-existing manual score placeholder.

        Admin score entry creates offline attempts with ``submission_id=0``.
        In production OMR flows, a teacher can enter a temporary 0 before the
        actual OMR scan finishes. That placeholder should not consume the only
        allowed attempt and block the real OMR submission. Manual subjective
        scores are component scores, so they can attach and be preserved even
        when non-zero. Non-zero manual total/objective scores are treated as
        deliberate score entries and are not overwritten.
        """
        qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=int(exam_id), enrollment_id=int(enrollment_id))
        )
        placeholder = (
            qs.filter(
                submission_id=0,
                attempt_index=1,
                is_representative=True,
            )
            .order_by("id")
            .first()
        )
        if not placeholder:
            return None

        meta = dict(placeholder.meta or {}) if isinstance(placeholder.meta, dict) else {}
        initial = meta.get("initial_snapshot") if isinstance(meta.get("initial_snapshot"), dict) else {}
        source = str(initial.get("source") or "")
        initial_total = _safe_float(initial.get("total_score"))
        if source not in _ATTACHABLE_MANUAL_SOURCES:
            return None
        if source != "admin_manual_subjective" and initial_total != 0.0:
            return None

        from apps.domains.results.models import Result, ResultItem

        result = (
            Result.objects
            .select_for_update()
            .filter(
                target_type="exam",
                target_id=int(exam_id),
                enrollment_id=int(enrollment_id),
                attempt_id=int(placeholder.id),
            )
            .first()
        )
        if result:
            if _safe_float(result.objective_score) != 0.0:
                return None
            if ResultItem.objects.filter(result=result).exists():
                return None
            if source != "admin_manual_subjective" and _safe_float(result.total_score) != 0.0:
                return None

        meta["manual_score_placeholder"] = {
            "attached_submission_id": int(submission_id),
            "attached_at": timezone.now().isoformat(),
            "previous_submission_id": 0,
            "previous_initial_snapshot": initial,
        }
        placeholder.submission_id = int(submission_id)
        placeholder.status = "pending"
        placeholder.meta = meta
        placeholder.save(update_fields=["submission_id", "status", "meta", "updated_at"])
        return placeholder


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
