# apps/domains/results/utils/ranking.py
"""
석차(등수) 계산 유틸리티.

설계 원칙:
- 대표 Result(enrollment당 최신 Result) 기준 석차.
  · 일반 플로우에서 Result는 1차 submission으로 생성되어 "석차=1차 점수"가 성립한다.
  · 그러나 ONLINE 재응시 시 `sync_result_from_exam_submission`이 Result.total_score를
    덮어쓰면 석차 기준값이 재응시 점수로 대체된다.
  · 메모리 정책 ("석차=1차, 성취=전체 이력")을 엄격히 지키려면 Result 대신
    ExamAttempt.attempt_index=1 기반으로 재설계해야 한다 — 정책 재확인 필요.
  · 완화책: sync는 이전 대표 attempt.meta.final_result_snapshot에 기존 점수를 백업함.
- dense_rank: 동점이면 같은 등수, 다음 등수는 건너뛰지 않음
- query-time 계산: Result 모델에 필드 추가 없음
- tenant isolation: 호출부에서 보장 (이 유틸은 Result queryset만 받음)
- NOT_SUBMITTED(미응시) 학생은 석차에서 제외
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.models import Result, ExamAttempt
from django.db.models import Max, Subquery


def _get_not_submitted_enrollment_ids(attempt_ids: list) -> Set[int]:
    """NOT_SUBMITTED ExamAttempt의 enrollment_id 집합 반환."""
    if not attempt_ids:
        return set()
    return set(
        ExamAttempt.objects.filter(
            id__in=attempt_ids,
            meta__status="NOT_SUBMITTED",
        ).values_list("enrollment_id", flat=True)
    )


def compute_exam_rankings(
    *,
    exam_id: int,
) -> Dict[int, dict]:
    """
    시험별 enrollment_id → {rank, percentile, cohort_size, cohort_avg} 맵 반환.

    - rank: dense_rank (동점 = 같은 등수)
    - percentile: 상위 % (1등/20명 = 5%, 낮을수록 좋음)
    - cohort_size: 총 응시자 수 (미응시 제외)
    - cohort_avg: 평균 점수

    NOT_SUBMITTED(미응시) 학생은 석차 계산에서 제외된다.
    """
    rs = latest_results_per_enrollment(target_type="exam", target_id=int(exam_id))
    base_qs = rs.exclude(enrollment_id__isnull=True)

    # NOT_SUBMITTED attempt 제외
    attempt_ids = list(
        base_qs.exclude(attempt_id__isnull=True)
        .values_list("attempt_id", flat=True)
    )
    not_submitted_eids = _get_not_submitted_enrollment_ids(attempt_ids)

    rows = list(
        base_qs.exclude(enrollment_id__in=not_submitted_eids)
        .values("enrollment_id", "total_score")
        .order_by("-total_score", "enrollment_id")
    ) if not_submitted_eids else list(
        base_qs.values("enrollment_id", "total_score")
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

        # 상위 %: rank/cohort_size * 100 (1등/20명 = 5%)
        if cohort_size <= 1:
            percentile = 100.0
        else:
            percentile = round((current_rank / cohort_size) * 100, 1)

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
    NOT_SUBMITTED(미응시) 학생은 석차 계산에서 제외된다.
    """
    if not exam_ids:
        return {}

    _enrollment_set: Optional[Set[int]] = set(enrollment_ids) if enrollment_ids is not None else None

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

    latest_results = Result.objects.filter(id__in=Subquery(latest_ids))

    # NOT_SUBMITTED attempt 제외
    attempt_ids = list(
        latest_results.exclude(attempt_id__isnull=True)
        .values_list("attempt_id", flat=True)
    )
    not_submitted_eids = _get_not_submitted_enrollment_ids(attempt_ids)

    qs = latest_results.exclude(enrollment_id__in=not_submitted_eids) if not_submitted_eids else latest_results

    rows = list(
        qs.values("target_id", "enrollment_id", "total_score")
        .order_by("target_id", "-total_score", "enrollment_id")
    )

    # exam_id별 그룹핑
    by_exam: Dict[int, list] = {}
    for r in rows:
        eid = int(r["target_id"])
        by_exam.setdefault(eid, []).append(r)

    result: Dict[int, Dict[int, dict]] = {}
    for exam_id_key, exam_rows in by_exam.items():
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
                percentile = round((current_rank / cohort_size) * 100, 1)

            if _enrollment_set is None or enroll_id in _enrollment_set:
                exam_map[enroll_id] = {
                    "rank": current_rank,
                    "percentile": percentile,
                    "cohort_size": cohort_size,
                    "cohort_avg": cohort_avg,
                }

        result[exam_id_key] = exam_map

    return result
