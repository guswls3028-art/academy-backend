# apps/domains/results/utils/ranking.py
"""
석차(등수) 계산 유틸리티.

설계 원칙:
- 1차 시도(attempt_index=1) 기준 석차 (score-clinic philosophy)
- dense_rank: 동점이면 같은 등수, 다음 등수는 건너뛰지 않음
- query-time 계산: Result 모델에 필드 추가 없음
- tenant isolation: 호출부에서 보장 (이 유틸은 Result queryset만 받음)
"""
from __future__ import annotations

from typing import Dict, List, Optional

from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.models import Result
from django.db.models import Max, Subquery


def compute_exam_rankings(
    *,
    exam_id: int,
) -> Dict[int, dict]:
    """
    시험별 enrollment_id → {rank, percentile, cohort_size, cohort_avg} 맵 반환.

    - rank: dense_rank (동점 = 같은 등수)
    - percentile: 상위 백분위 (1등 = 작은 값, 꼴찌 = 100에 가까움)
      공식: ((cohort_size - rank) / (cohort_size - 1)) * 100  (단, 1명이면 100)
    - cohort_size: 총 응시자 수
    - cohort_avg: 평균 점수

    반환 예:
    {
      enrollment_id: {
          "rank": 1,
          "percentile": 95.0,  # 상위 5% → 95 percentile
          "cohort_size": 20,
          "cohort_avg": 72.5,
      }
    }
    """
    rs = latest_results_per_enrollment(target_type="exam", target_id=int(exam_id))
    rows = list(
        rs.exclude(enrollment_id__isnull=True)
        .values("enrollment_id", "total_score")
        .order_by("-total_score", "enrollment_id")
    )

    if not rows:
        return {}

    cohort_size = len(rows)
    total = sum(float(r["total_score"] or 0) for r in rows)
    cohort_avg = round(total / cohort_size, 2) if cohort_size else 0.0

    # dense_rank 계산
    result_map: Dict[int, dict] = {}
    current_rank = 0
    prev_score: Optional[float] = None

    for r in rows:
        score = float(r["total_score"] or 0)
        eid = int(r["enrollment_id"])

        if prev_score is None or score != prev_score:
            current_rank += 1
            prev_score = score

        # percentile: 상위 몇 %에 해당하는지
        # rank 1 = 상위 (1/cohort_size)*100 %
        if cohort_size <= 1:
            percentile = 100.0
        else:
            # 상위 백분위: rank가 낮을수록 높은 percentile
            percentile = round(((cohort_size - current_rank) / (cohort_size - 1)) * 100, 1)

        result_map[eid] = {
            "rank": current_rank,
            "percentile": percentile,
            "cohort_size": cohort_size,
            "cohort_avg": cohort_avg,
        }

    return result_map


def compute_exam_rankings_batch(
    *,
    exam_ids: List[int],
    enrollment_ids: Optional[List[int]] = None,
) -> Dict[int, Dict[int, dict]]:
    """
    여러 시험의 석차를 한 번에 계산. N+1 쿼리 방지용.

    반환: { exam_id: { enrollment_id: {rank, percentile, cohort_size, cohort_avg} } }

    enrollment_ids가 주어지면 해당 enrollment의 석차만 반환 (계산은 전체 대상).
    단일 쿼리로 모든 시험의 Result를 가져와서 Python에서 그룹핑.
    """
    if not exam_ids:
        return {}

    # 모든 시험의 latest Result를 한 번에 조회
    base = Result.objects.filter(
        target_type="exam",
        target_id__in=[int(eid) for eid in exam_ids],
    ).exclude(enrollment_id__isnull=True)

    # enrollment별 최신 Result.id 선택 (시험별로)
    latest_ids = (
        base.values("target_id", "enrollment_id")
        .annotate(last_id=Max("id"))
        .values("last_id")
    )

    rows = list(
        Result.objects.filter(id__in=Subquery(latest_ids))
        .values("target_id", "enrollment_id", "total_score")
        .order_by("target_id", "-total_score", "enrollment_id")
    )

    # exam_id별 그룹핑
    by_exam: Dict[int, list] = {}
    for r in rows:
        eid = int(r["target_id"])
        by_exam.setdefault(eid, []).append(r)

    result: Dict[int, Dict[int, dict]] = {}
    for exam_id, exam_rows in by_exam.items():
        cohort_size = len(exam_rows)
        total = sum(float(r["total_score"] or 0) for r in exam_rows)
        cohort_avg = round(total / cohort_size, 2) if cohort_size else 0.0

        exam_map: Dict[int, dict] = {}
        current_rank = 0
        prev_score: Optional[float] = None

        for r in exam_rows:
            score = float(r["total_score"] or 0)
            enroll_id = int(r["enrollment_id"])

            if prev_score is None or score != prev_score:
                current_rank += 1
                prev_score = score

            if cohort_size <= 1:
                percentile = 100.0
            else:
                percentile = round(((cohort_size - current_rank) / (cohort_size - 1)) * 100, 1)

            # enrollment_ids 필터: 주어진 경우 해당 enrollment만 결과에 포함
            if enrollment_ids is None or enroll_id in enrollment_ids:
                exam_map[enroll_id] = {
                    "rank": current_rank,
                    "percentile": percentile,
                    "cohort_size": cohort_size,
                    "cohort_avg": cohort_avg,
                }

        result[exam_id] = exam_map

    return result
