from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median, pstdev, quantiles
from typing import Any

from apps.domains.results.models import ExamAttempt
from apps.domains.results.utils.exam_achievement import compute_exam_achievement_bulk
from apps.domains.results.utils.initial_exam_score import (
    InitialExamScore,
    load_initial_exam_scores,
    project_initial_exam_score,
)
from apps.domains.results.utils.result_queries import latest_results_for_targets_per_enrollment
from apps.support.results.admin_student_grades_dependencies import (
    primary_session_metadata_by_exam_and_lecture,
    prior_analysis_exam_sessions,
)
from apps.support.results.exam_policy_dependencies import (
    effective_exam_pass_scores,
    sessions_by_id_for_tenant,
)
from apps.support.results.exam_result_excel_import_dependencies import (
    ResultImportCandidateRecord,
    get_result_import_candidates,
)

TREND_THRESHOLD_PP = 10


@dataclass(frozen=True)
class AnalysisScore:
    initial: InitialExamScore
    status: str
    achievement: dict[str, Any]


@dataclass(frozen=True)
class ExamAnalysis:
    candidates: dict[int, ResultImportCandidateRecord]
    results: dict[int, Any]
    score_by_enrollment: dict[int, AnalysisScore]
    pass_scores_by_lecture: dict[int, float]
    insights: dict[str, Any]


def score_rate(score: float, maximum: float) -> float:
    return max(0.0, min(100.0, score / maximum * 100.0))


def recommended_cutoff(average: float | None, maximum: float) -> int | None:
    if average is None or not math.isfinite(maximum) or maximum < 5:
        return None
    # Positive half ties round up, even when the multiple of five is odd.
    rounded = math.floor(average / 5 + 0.5) * 5
    return min(rounded, math.floor(maximum / 5) * 5) if rounded > 0 else None


def _score_summary(
    *, confirmed: dict[int, AnalysisScore], pass_by_enrollment: dict[int, float], roster_count: int,
) -> dict[str, Any]:
    raw_scores = []
    score_rates = []
    distribution_counts = [0] * 5
    pass_criterion_count = pass_count = remediated_count = 0
    for enrollment_id, row in confirmed.items():
        score = row.initial.total_score
        rate = score_rate(score, row.initial.max_score)
        raw_scores.append(score)
        score_rates.append(rate)
        distribution_counts[max(0, math.ceil(rate / 20) - 1)] += 1
        criterion = pass_by_enrollment[enrollment_id]
        if criterion > 0:
            pass_criterion_count += 1
            pass_count += score >= criterion
        remediated_count += bool(row.achievement.get("remediated"))

    ordered = sorted(raw_scores)
    top_count = math.ceil(len(ordered) / 10)
    quartiles = quantiles(ordered, n=4, method="inclusive") if len(ordered) >= 4 else None
    return {
        "average": mean(ordered) if ordered else None,
        "median": median(ordered) if ordered else None,
        "top_ten_average": mean(ordered[-top_count:]) if ordered else None,
        "highest": ordered[-1] if ordered else None,
        "std_dev": pstdev(ordered) if ordered else None,
        "std_rate": pstdev(score_rates) if score_rates else None,
        "p25": quartiles[0] if quartiles else None,
        "p75": quartiles[2] if quartiles else None,
        "unscored_count": roster_count - len(confirmed),
        "pass_criterion_count": pass_criterion_count,
        "pass_count": pass_count,
        "remediated_count": remediated_count,
        "distribution_counts": distribution_counts,
    }


def _load_scores(*, results, exam_maxima, tenant, candidate_policy=None):
    if not results:
        return {}
    states = load_initial_exam_scores(
        exam_ids=exam_maxima,
        enrollment_ids={result.enrollment_id for result in results},
    )
    attempts = ExamAttempt.objects.in_bulk(
        state.attempt_id for state in states.values() if state.attempt_id is not None
    )
    projected_by_pair = {}
    for result in results:
        key = (result.target_id, result.enrollment_id)
        state = states.get(key)
        matches_initial = result.attempt_id is None or (
            state is not None and result.attempt_id == state.attempt_id
        )
        projected_by_pair[key] = project_initial_exam_score(
            state=state,
            fallback_score=result.total_score if matches_initial else None,
            fallback_max_score=(result.max_score if matches_initial else None) or exam_maxima[result.target_id],
            fallback_recorded_at=result.submitted_at or result.created_at,
        )

    items = []
    for (exam_id, enrollment_id), score in projected_by_pair.items():
        pass_score, session = candidate_policy(enrollment_id) if candidate_policy else (None, None)
        items.append({
            "exam_id": exam_id,
            "enrollment_id": enrollment_id,
            "attempt_id": score.attempt_id,
            "total_score": score.total_score,
            "pass_score": pass_score,
            "session_id": session.id if session is not None else None,
        })
    achievements = compute_exam_achievement_bulk(
        items=items, tenant=tenant, include_first_attempt_readiness=True,
    )
    rows = {}
    for result in results:
        exam_id, enrollment_id = result.target_id, result.enrollment_id
        key = (exam_id, enrollment_id)
        score = projected_by_pair[key]
        achievement = achievements.get((enrollment_id, exam_id), {})
        attempt = attempts.get(score.attempt_id) or result.attempt
        attempt_status = getattr(attempt, "status", None)
        if score.not_submitted or achievement.get("meta_status") == "NOT_SUBMITTED":
            status = "NOT_SUBMITTED"
        elif attempt_status in {"pending", "grading"}:
            status = "PROCESSING"
        elif attempt_status == "failed":
            status = "FAILED"
        elif score.total_score is None or score.max_score is None or achievement.get("first_attempt_provisional"):
            status = "PARTIAL"
        else:
            status = "DONE"
        rows[key] = AnalysisScore(score, status, achievement)
    return rows


def _trend_signal(previous_delta: float | None, cumulative_delta: float) -> str | None:
    comparisons = [cumulative_delta] if previous_delta is None else [cumulative_delta, previous_delta]
    if all(delta >= TREND_THRESHOLD_PP for delta in comparisons):
        return "up"
    if all(delta <= -TREND_THRESHOLD_PP for delta in comparisons):
        return "down"
    return None


def _trends(*, tenant, exam, candidates, scores, current_sessions):
    previous_order, exam_order = prior_analysis_exam_sessions(
        tenant=tenant, exam=exam, current_sessions=current_sessions,
    )
    candidate_ids = {
        enrollment_id for enrollment_id, score in scores.items()
        if score.status == "DONE" and candidates[enrollment_id].lecture_id in previous_order
    }
    if not exam_order or not candidate_ids:
        return []

    prior_maxima = {exam_id: float(maximum or 100.0) for (exam_id, _), (_, maximum) in exam_order.items()}
    prior_results = [
        result for result in latest_results_for_targets_per_enrollment(
            target_type="exam", target_ids=prior_maxima,
        ).filter(enrollment_id__in=candidate_ids, enrollment__tenant=tenant).select_related("attempt")
        if (result.target_id, candidates[result.enrollment_id].lecture_id) in exam_order
    ]
    history = defaultdict(list)
    for (exam_id, enrollment_id), row in _load_scores(
        results=prior_results, exam_maxima=prior_maxima, tenant=tenant,
    ).items():
        if row.status == "DONE":
            lecture_id = candidates[enrollment_id].lecture_id
            order, _ = exam_order[(exam_id, lecture_id)]
            history[enrollment_id].append((
                order, score_rate(row.initial.total_score, row.initial.max_score),
            ))

    trends = []
    for enrollment_id, records in history.items():
        candidate = candidates[enrollment_id]
        current = scores[enrollment_id].initial
        current_rate = score_rate(current.total_score, current.max_score)
        previous = [rate for order, rate in records if order == previous_order[candidate.lecture_id]]
        previous_rate = round(mean(previous), 1) if previous else None
        cumulative_rate = round(mean(rate for _, rate in records), 1)
        current_rate = round(current_rate, 1)
        previous_delta = round(current_rate - previous_rate, 1) if previous_rate is not None else None
        cumulative_delta = round(current_rate - cumulative_rate, 1)
        trends.append({
            "enrollment_id": enrollment_id,
            "student_name": candidate.student_name,
            "lecture_title": candidate.lecture_title,
            "current_rate": current_rate,
            "previous_session_rate": previous_rate,
            "cumulative_rate": cumulative_rate,
            "prior_exam_count": len(records),
            "previous_delta_pp": previous_delta,
            "cumulative_delta_pp": cumulative_delta,
            "signal": _trend_signal(previous_delta, cumulative_delta),
        })
    return sorted(trends, key=lambda row: (
        row["signal"] is None, -abs(row["cumulative_delta_pp"]),
        row["student_name"], row["enrollment_id"],
    ))


def load_exam_analysis(
    *, exam, tenant, lecture_id: int | None = None, include_items: bool = False,
) -> ExamAnalysis:
    """Read the same roster, confirmed first attempts and trends for web and Excel."""
    candidates = {
        candidate.enrollment_id: candidate
        for candidate in get_result_import_candidates(exam_id=exam.id, tenant=tenant)
        if lecture_id is None or candidate.lecture_id == lecture_id
    }
    lecture_ids = {candidate.lecture_id for candidate in candidates.values() if candidate.lecture_id is not None}
    pass_scores = effective_exam_pass_scores(exam=exam, lecture_ids=lecture_ids)
    session_meta = primary_session_metadata_by_exam_and_lecture(
        tenant=tenant, exam_lecture_pairs={(exam.id, value) for value in lecture_ids},
    )
    session_ids_by_lecture = {
        value: meta["session_id"] for (_, value), meta in session_meta.items() if meta.get("session_id") is not None
    }
    sessions = sessions_by_id_for_tenant(session_ids=set(session_ids_by_lecture.values()), tenant=tenant)
    current_sessions = {
        value: sessions[session_id] for value, session_id in session_ids_by_lecture.items()
        if session_id in sessions
    }

    default_pass_score = float(exam.pass_score or 0.0)
    pass_by_enrollment = {
        enrollment_id: pass_scores.get(candidate.lecture_id, default_pass_score)
        for enrollment_id, candidate in candidates.items()
    }

    def candidate_policy(enrollment_id):
        return pass_by_enrollment[enrollment_id], current_sessions.get(candidates[enrollment_id].lecture_id)

    results = latest_results_for_targets_per_enrollment(
        target_type="exam", target_ids=[exam.id],
    ).filter(
        enrollment__tenant=tenant, enrollment_id__in=candidates,
    ).select_related("attempt")
    if include_items:
        results = results.prefetch_related("items")
    results = {result.enrollment_id: result for result in results}
    maximum = float(exam.max_score or 100.0)
    scores = {
        enrollment_id: entry
        for (_, enrollment_id), entry in _load_scores(
            results=list(results.values()), exam_maxima={exam.id: maximum},
            tenant=tenant, candidate_policy=candidate_policy,
        ).items()
    }
    confirmed = {enrollment_id: row for enrollment_id, row in scores.items() if row.status == "DONE"}
    summary = _score_summary(
        confirmed=confirmed, pass_by_enrollment=pass_by_enrollment, roster_count=len(candidates),
    )
    cutoff = recommended_cutoff(summary["average"], maximum)
    return ExamAnalysis(
        candidates=candidates,
        results=results,
        score_by_enrollment=scores,
        pass_scores_by_lecture=pass_scores,
        insights={
            "recommended_cutoff": cutoff,
            "below_recommended_count": (
                sum(row.initial.total_score < cutoff for row in confirmed.values()) if cutoff is not None else None
            ),
            "scored_count": len(confirmed),
            "score_summary": summary,
            "trend_threshold_pp": TREND_THRESHOLD_PP,
            "trends": _trends(
                tenant=tenant, exam=exam, candidates=candidates,
                scores=scores, current_sessions=current_sessions,
            ) if confirmed else [],
        },
    )
