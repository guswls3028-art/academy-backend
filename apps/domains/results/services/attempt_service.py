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
