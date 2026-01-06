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

    ✅ 운영 안전성 패치 (Critical #4)
    - ResultFact / ResultItem에 들어가는 값은 "항상 타입이 보장된다"는 전제가 운영에서 깨질 수 있음
      (blank, meta-only, None, 숫자/리스트 등)
    - 따라서 여기서 최소 캐스팅/디폴트를 강제해 DB insert 안정성을 올린다.
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
        items format (권장):
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

        # ✅ 대표 attempt 추적 (덮어쓰는게 정상)
        result.attempt_id = int(attempt_id)

        total = 0.0
        max_total = 0.0

        for item in (items or []):
            # -----------------------------
            # ✅ Critical #4 PATCH
            # -----------------------------
            qid = int(item.get("question_id"))
            ans = str(item.get("answer") or "")
            is_correct = bool(item.get("is_correct"))
            score = float(item.get("score") or 0.0)
            max_score = float(item.get("max_score") or 0.0)
            source = str(item.get("source") or "")
            meta = item.get("meta", None)

            # 1️⃣ Fact (append-only)
            ResultFact.objects.create(
                target_type=target_type,
                target_id=int(target_id),
                enrollment_id=int(enrollment_id),
                submission_id=int(submission_id),
                attempt_id=int(attempt_id),

                question_id=qid,
                answer=ans,
                is_correct=is_correct,
                score=score,
                max_score=max_score,
                source=source,
                meta=meta,
            )

            # 2️⃣ Snapshot (ResultItem)
            ResultItem.objects.update_or_create(
                result=result,
                question_id=qid,
                defaults={
                    "answer": ans,
                    "is_correct": is_correct,
                    "score": score,
                    "max_score": max_score,
                    "source": source,
                },
            )

            total += score
            max_total += max_score

        result.total_score = float(total)
        result.max_score = float(max_total)
        result.submitted_at = timezone.now()

        result.save(
            update_fields=["attempt_id", "total_score", "max_score", "submitted_at"]
        )

        return result
