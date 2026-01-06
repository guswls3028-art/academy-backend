# apps/domains/results/services/applier.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.domains.results.models import Result, ResultItem, ResultFact


class ResultApplier:
    """
    계산된 결과를 받아 results에 반영
    ❌ 계산 없음 (계산은 grader가 함)

    ✅ attempt 중심 설계 반영:
    - apply()가 attempt_id를 받아서 Result / ResultFact에 저장
    """

    @staticmethod
    @transaction.atomic
    def apply(
        *,
        target_type: str,
        target_id: int,
        enrollment_id: int,
        submission_id: int,
        attempt_id: int,            # ✅ 추가
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

        # Result는 "최신 스냅샷"
        result, _ = Result.objects.get_or_create(
            target_type=target_type,
            target_id=target_id,
            enrollment_id=enrollment_id,
        )

        # ✅ 이 Result가 참조하는 attempt 갱신
        # 대표 attempt가 바뀌면 여기서 최신값으로 덮어씌워짐(정상)
        result.attempt_id = int(attempt_id)

        total = 0.0
        max_total = 0.0

        for item in items:
            # 1️⃣ Fact (append-only)
            ResultFact.objects.create(
                target_type=target_type,
                target_id=target_id,
                enrollment_id=enrollment_id,
                submission_id=submission_id,
                attempt_id=int(attempt_id),  # ✅ 추가
                **item,
            )

            # 2️⃣ Snapshot (ResultItem)
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

            total += float(item["score"] or 0.0)
            max_total += float(item["max_score"] or 0.0)

        result.total_score = float(total)
        result.max_score = float(max_total)
        result.submitted_at = timezone.now()

        # ✅ attempt_id도 같이 저장
        result.save(
            update_fields=["attempt_id", "total_score", "max_score", "submitted_at"]
        )

        return result
