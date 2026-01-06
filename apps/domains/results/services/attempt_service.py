# apps/domains/results/services/attempt_service.py
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.domains.results.models import ExamAttempt
from apps.domains.exams.models import Exam


class ExamAttemptService:
    """
    ExamAttempt 생성/관리 전담

    🔥 Critical 패치:
    - 같은 submission_id로 Attempt가 중복 생성되는 것을 차단
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
        # 🔴 CRITICAL #2
        # -------------------------------------------------
        # 같은 submission으로 Attempt가 이미 있으면 즉시 차단
        if ExamAttempt.objects.filter(submission_id=int(submission_id)).exists():
            raise ValidationError(
                "Attempt already exists for this submission."
            )

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
        # 3️⃣ 동시성 안전: (exam, enrollment) lock
        # -------------------------------------------------
        qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
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
        # 5️⃣ 대표 attempt 교체
        # -------------------------------------------------
        qs.filter(is_representative=True).update(is_representative=False)

        attempt = ExamAttempt.objects.create(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
            submission_id=submission_id,
            attempt_index=next_index,
            is_retake=(last > 0),
            is_representative=True,
            status="pending",
        )

        return attempt
