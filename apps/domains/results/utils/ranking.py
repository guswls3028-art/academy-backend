# apps/domains/results/utils/ranking.py
"""
석차(등수) 계산 유틸리티.

설계 원칙:
- "석차=1차 점수" 정책 (score-clinic philosophy).
  · 1차 attempt의 `meta.initial_snapshot.total_score`를 최우선 소스로 사용.
  · `sync_result_from_exam_submission`이 attempt_index=1 생성 시 해당 snapshot을
    저장하므로, 이후 ONLINE 재응시로 Result.total_score가 덮어쓰여져도 1차 점수는
    불변으로 유지된다.
  · initial_snapshot이 없는 legacy 데이터는 Result.total_score로 fallback
    (attempt_index=1 도입 이전 데이터 호환).
- dense_rank: 동점이면 같은 등수, 다음 등수는 건너뛰지 않음.
- query-time 계산: Result 모델에 필드 추가 없음.
- tenant isolation: 호출부에서 보장 (이 유틸은 exam_id 단위로 동작).
- NOT_SUBMITTED(미응시) 학생은 석차 계산에서 제외.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

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


def _first_attempt_score_override(
    *,
    exam_ids: Iterable[int],
    enrollment_ids: Optional[Iterable[int]] = None,
) -> Dict[int, Dict[int, float]]:
    """
    {exam_id: {enrollment_id: 1차 점수}} 매핑을 반환.

    `ExamAttempt(attempt_index=1).meta["initial_snapshot"]["total_score"]`를 소스로 사용.
    값이 없는 경우는 이 맵에 없음 → 호출부에서 Result.total_score로 fallback.
    NOT_SUBMITTED attempt는 맵에 포함하지 않아 자연스럽게 제외된다.
    """
    exam_ids_int = [int(x) for x in exam_ids]
    if not exam_ids_int:
        return {}

    qs = ExamAttempt.objects.filter(
        exam_id__in=exam_ids_int,
        attempt_index=1,
    )
    if enrollment_ids is not None:
        qs = qs.filter(enrollment_id__in=[int(x) for x in enrollment_ids])

    override: Dict[int, Dict[int, float]] = {eid: {} for eid in exam_ids_int}
    for att in qs.only("exam_id", "enrollment_id", "meta"):
        meta = att.meta if isinstance(att.meta, dict) else {}
        if meta.get("status") == "NOT_SUBMITTED":
            continue
        snapshot = meta.get("initial_snapshot")
        if not isinstance(snapshot, dict):
            continue
        raw = snapshot.get("total_score")
        if raw is None:
            continue
        try:
            override[int(att.exam_id)][int(att.enrollment_id)] = float(raw)
        except (TypeError, ValueError):
            continue
    return override


def _dense_rank(
    rows: List[dict],
) -> Dict[int, dict]:
    """공통 dense_rank 계산. rows: [{enrollment_id, total_score}] (내림차순 정렬 전제)."""
    if not rows:
        return {}

    cohort_size = len(rows)
    total = sum(float(r["total_score"] or 0) for r in rows)
    cohort_avg = round(total / cohort_size, 2) if cohort_size else 0.0

    result_map: Dict[int, dict] = {}
    current_rank = 0
    prev_score: Optional[float] = None
    for r in rows:
        score = float(r["total_score"] or 0)
        eid = int(r["enrollment_id"])
        if prev_score is None or score != prev_score:
            current_rank += 1
            prev_score = score
        percentile = 100.0 if cohort_size <= 1 else round((current_rank / cohort_size) * 100, 1)
        result_map[eid] = {
            "rank": current_rank,
            "percentile": percentile,
            "cohort_size": cohort_size,
            "cohort_avg": cohort_avg,
        }
    return result_map


def compute_exam_rankings(
    *,
    exam_id: int,
) -> Dict[int, dict]:
    """
    시험별 enrollment_id → {rank, percentile, cohort_size, cohort_avg} 맵 반환.

    - rank: dense_rank (동점 = 같은 등수)
    - percentile: 상위 % (1등/20명 = 5%, 낮을수록 좋음)
    - cohort_size: 총 응시자 수 (미응시 제외)
    - cohort_avg: 평균 점수 (1차 점수 기준)

    NOT_SUBMITTED(미응시) 학생은 석차 계산에서 제외된다.
    """
    exam_id = int(exam_id)
    rs = latest_results_per_enrollment(target_type="exam", target_id=exam_id)
    base_qs = rs.exclude(enrollment_id__isnull=True)

    # NOT_SUBMITTED attempt 제외
    attempt_ids = list(
        base_qs.exclude(attempt_id__isnull=True)
        .values_list("attempt_id", flat=True)
    )
    not_submitted_eids = _get_not_submitted_enrollment_ids(attempt_ids)

    raw_rows = list(
        (base_qs.exclude(enrollment_id__in=not_submitted_eids) if not_submitted_eids else base_qs)
        .values("enrollment_id", "total_score")
    )

    # 1차 점수 override: attempt_index=1.meta.initial_snapshot 우선
    override = _first_attempt_score_override(exam_ids=[exam_id]).get(exam_id, {})
    rows = []
    for r in raw_rows:
        eid = int(r["enrollment_id"])
        score = override.get(eid)
        if score is None:
            score = float(r["total_score"] or 0)
        rows.append({"enrollment_id": eid, "total_score": score})

    rows.sort(key=lambda r: (-float(r["total_score"] or 0), int(r["enrollment_id"])))
    return _dense_rank(rows)


def compute_exam_rankings_batch(
    *,
    exam_ids: List[int],
    enrollment_ids: Optional[List[int]] = None,
) -> Dict[int, Dict[int, dict]]:
    """
    여러 시험의 석차를 한 번에 계산. N+1 쿼리 방지용.

    반환: { exam_id: { enrollment_id: {rank, percentile, cohort_size, cohort_avg} } }

    enrollment_ids가 주어지면 해당 enrollment의 석차만 반환 (코호트 계산은 전체 대상).
    NOT_SUBMITTED(미응시) 학생은 석차 계산에서 제외된다.
    1차 점수(attempt_index=1 initial_snapshot) 우선, fallback Result.
    """
    if not exam_ids:
        return {}

    exam_ids_int = [int(eid) for eid in exam_ids]
    target_enrollment_set: Optional[Set[int]] = (
        set(int(x) for x in enrollment_ids) if enrollment_ids is not None else None
    )

    # 모든 시험의 latest Result를 한 번에 조회 (코호트 = 응시자 전체)
    base = Result.objects.filter(
        target_type="exam",
        target_id__in=exam_ids_int,
    ).exclude(enrollment_id__isnull=True)

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

    qs = (
        latest_results.exclude(enrollment_id__in=not_submitted_eids)
        if not_submitted_eids else latest_results
    )
    raw_rows = list(qs.values("target_id", "enrollment_id", "total_score"))

    # 1차 점수 override (exam_id × enrollment_id)
    override_map = _first_attempt_score_override(exam_ids=exam_ids_int)

    # exam_id별 그룹핑 + score override
    by_exam: Dict[int, list] = {}
    for r in raw_rows:
        eid_exam = int(r["target_id"])
        eid_enroll = int(r["enrollment_id"])
        score = override_map.get(eid_exam, {}).get(eid_enroll)
        if score is None:
            score = float(r["total_score"] or 0)
        by_exam.setdefault(eid_exam, []).append({
            "enrollment_id": eid_enroll,
            "total_score": score,
        })

    result: Dict[int, Dict[int, dict]] = {}
    for exam_id_key in exam_ids_int:
        exam_rows = by_exam.get(exam_id_key, [])
        exam_rows.sort(key=lambda r: (-float(r["total_score"] or 0), int(r["enrollment_id"])))
        rank_map = _dense_rank(exam_rows)
        if target_enrollment_set is None:
            result[exam_id_key] = rank_map
        else:
            result[exam_id_key] = {
                eid: info for eid, info in rank_map.items()
                if eid in target_enrollment_set
            }

    return result
