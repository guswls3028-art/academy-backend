from django.db import transaction
from django.db.models import Max

from apps.domains.results.models import ExamAttempt


class ExamAttemptService:
    """
    ExamAttempt 생성/관리 전담

    🔥 수정 사항
    - 동시성 안전 (transaction.atomic)
    - 대표 attempt 단일성 보장
    """

    @staticmethod
    @transaction.atomic
    def create_for_submission(
        *,
        exam_id: int,
        enrollment_id: int,
        submission_id: int,
    ) -> ExamAttempt:

        # 🔒 row-level lock
        qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
        )

        last = qs.aggregate(Max("attempt_index")).get(
            "attempt_index__max"
        ) or 0

        # 기존 대표 attempt 해제
        qs.filter(is_representative=True).update(
            is_representative=False
        )

        attempt = ExamAttempt.objects.create(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
            submission_id=submission_id,
            attempt_index=last + 1,
            is_retake=(last > 0),
            is_representative=True,
            status="pending",
        )

        return attempt
