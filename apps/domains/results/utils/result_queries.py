# apps/domains/results/utils/result_queries.py
from __future__ import annotations

from collections.abc import Iterable

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
    return latest_results_for_targets_per_enrollment(
        target_type=target_type,
        target_ids=[target_id],
    )


def latest_results_for_targets_per_enrollment(
    *,
    target_type: str,
    target_ids: Iterable[int],
) -> QuerySet[Result]:
    """Return one latest Result for every target/enrollment pair in one query."""
    normalized_target_ids = sorted({int(target_id) for target_id in target_ids})
    if not normalized_target_ids:
        return Result.objects.none()

    base = Result.objects.filter(
        target_type=str(target_type),
        target_id__in=normalized_target_ids,
    )

    # target/enrollment별 가장 최신 Result.id를 선택한다. target을 함께 묶어야
    # 여러 시험을 한 번에 읽어도 다른 시험의 대표 결과가 서로 덮이지 않는다.
    latest_ids = (
        base.values("target_id", "enrollment_id")
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
