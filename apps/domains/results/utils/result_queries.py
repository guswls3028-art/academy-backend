# apps/domains/results/utils/result_queries.py
from __future__ import annotations

from django.db.models import Max, QuerySet, Subquery

from apps.domains.results.models import Result


def latest_results_per_enrollment(
    *,
    target_type: str,
    target_id: int,
) -> QuerySet[Result]:
    """
    ✅ 통계/집계에서 사용하는 '최신 Result' queryset (enrollment 기준 1개)

    왜 필요한가?
    - unique_together가 있어도 운영에서는:
        - 과거 데이터 깨짐
        - manual insert
        - 장애 복구/마이그레이션 실수
      로 동일 enrollment의 Result가 중복될 수 있다.
    - 통계는 중복을 고려하지 않으면 participant/avg/min/max 전부 왜곡.

    구현 방식:
    - enrollment_id별로 가장 큰 id(가장 최근 insert)를 선택
    - DB vendor 독립 (Postgres의 distinct on 같은 기능에 의존하지 않음)
    """
    target_id = int(target_id)

    base = Result.objects.filter(
        target_type=str(target_type),
        target_id=target_id,
    )

    # enrollment별 가장 최신 Result.id를 선택
    latest_ids = (
        base.values("enrollment_id")
        .annotate(last_id=Max("id"))
        .values("last_id")
    )

    return Result.objects.filter(id__in=Subquery(latest_ids))


def participant_count_distinct_enrollment(
    *,
    target_type: str,
    target_id: int,
) -> int:
    """
    ✅ participant_count 단일 규칙: distinct enrollment 기준
    """
    return (
        Result.objects.filter(
            target_type=str(target_type),
            target_id=int(target_id),
        )
        .values("enrollment_id")
        .distinct()
        .count()
    )
