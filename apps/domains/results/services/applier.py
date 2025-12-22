from django.db import transaction
from django.utils import timezone

from apps.domains.results.models import Result, ResultItem, ResultFact


class ResultApplier:
    """
    계산된 결과를 받아 results에 반영
    ❌ 계산 없음
    """

    @staticmethod
    @transaction.atomic
    def apply(
        *,
        target_type: str,
        target_id: int,
        enrollment_id: int,
        submission_id: int,
        items: list[dict],
    ) -> Result:
        """
        items format:
        {
            question_id,
            answer,
            is_correct,
            score,
            max_score,
            source,
            meta
        }
        """

        result, _ = Result.objects.get_or_create(
            target_type=target_type,
            target_id=target_id,
            enrollment_id=enrollment_id,
        )

        total = 0.0
        max_total = 0.0

        for item in items:
            # 1️⃣ Fact
            ResultFact.objects.create(
                target_type=target_type,
                target_id=target_id,
                enrollment_id=enrollment_id,
                submission_id=submission_id,
                **item,
            )

            # 2️⃣ Snapshot
            ResultItem.objects.update_or_create(
                result=result,
                question_id=item["question_id"],
                defaults={
                    "answer": item["answer"],
                    "is_correct": item["is_correct"],
                    "score": item["score"],
                    "max_score": item["max_score"],
                    "source": item["source"],
                },
            )

            total += item["score"]
            max_total += item["max_score"]

        result.total_score = total
        result.max_score = max_total
        result.submitted_at = timezone.now()
        result.save(update_fields=["total_score", "max_score", "submitted_at"])

        return result
