# PATH: apps/domains/results/aggregations/lecture_results.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.db.models import Avg, Min, Max, Count
from django.utils import timezone

from apps.domains.lectures.models import Lecture, Session
from apps.domains.progress.models import SessionProgress, ClinicLink, ProgressPolicy

from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_str(v: Any) -> str:
    try:
        return str(v or "")
    except Exception:
        return ""


def _policy_meta_for_lecture(lecture: Lecture) -> Dict[str, str]:
    try:
        policy = ProgressPolicy.objects.filter(lecture=lecture).first()
        strategy = _safe_str(getattr(policy, "exam_aggregate_strategy", "MAX") or "MAX")
        pass_source = _safe_str(getattr(policy, "exam_pass_source", "EXAM") or "EXAM")
        return {
            "strategy": strategy,
            "pass_source": pass_source,
        }
    except Exception:
        return {
            "strategy": "MAX",
            "pass_source": "EXAM",
        }


def build_lecture_results_snapshot(
    *,
    lecture_id: int,
    include_exam_level_stats: bool = False,
) -> Dict[str, Any]:
    """
    ✅ Lecture 단위 스냅샷 (세션 집계 기반)

    단일 진실:
    - 세션별 pass_rate: SessionProgress.exam_passed 기준
    - 세션별 clinic_rate: ClinicLink(is_auto=True) 기준
    - (옵션) 시험 단위 통계는 Result 기반 + latest_results_per_enrollment

    반환 스키마(고정):
    {
      "lecture_id": int,
      "strategy": str,
      "pass_source": str,
      "session_count": int,
      "sessions": [
        {
          "session_id": int,
          "order": int|None,
          "participant_count": int,
          "pass_rate": float,
          "clinic_rate": float,
          "exams": [ ... ] | []
        }
      ],
      "generated_at": "iso"
    }
    """
    lecture = Lecture.objects.filter(id=_safe_int(lecture_id)).first()
    if not lecture:
        return {
            "lecture_id": _safe_int(lecture_id),
            "strategy": "MAX",
            "pass_source": "EXAM",
            "session_count": 0,
            "sessions": [],
            "generated_at": timezone.now().isoformat(),
        }

    meta = _policy_meta_for_lecture(lecture)

    sessions_qs = Session.objects.filter(lecture=lecture).order_by("id")
    if hasattr(Session, "order"):
        try:
            sessions_qs = sessions_qs.order_by("order", "id")
        except Exception:
            sessions_qs = sessions_qs.order_by("id")

    rows: List[Dict[str, Any]] = []
    for s in sessions_qs:
        sp_qs = SessionProgress.objects.filter(session=s)
        participant_count = sp_qs.count()

        pass_count = sp_qs.filter(exam_passed=True).count()
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        clinic_count = (
            ClinicLink.objects.filter(session=s, is_auto=True)
            .values("enrollment_id").distinct().count()
        )
        clinic_rate = (clinic_count / participant_count) if participant_count else 0.0

        ex_rows: List[Dict[str, Any]] = []
        if include_exam_level_stats:
            exams = list(get_exams_for_session(s))
            for ex in exams:
                exid = _safe_int(getattr(ex, "id", 0))
                if not exid:
                    continue

                rs = latest_results_per_enrollment(target_type="exam", target_id=exid)

                agg = rs.aggregate(
                    participant_count=Count("id"),
                    avg_score=Avg("total_score"),
                    min_score=Min("total_score"),
                    max_score=Max("total_score"),
                )

                pass_score = _safe_float(getattr(ex, "pass_score", 0.0) or 0.0)
                pcount = rs.filter(total_score__gte=pass_score).count()
                fcount = rs.filter(total_score__lt=pass_score).count()

                p_total = _safe_int(agg["participant_count"] or 0)
                p_rate = (pcount / p_total) if p_total else 0.0

                ex_rows.append(
                    {
                        "exam_id": exid,
                        "title": _safe_str(getattr(ex, "title", "")),
                        "pass_score": float(pass_score),
                        "participant_count": int(p_total),
                        "avg_score": float(agg["avg_score"] or 0.0),
                        "min_score": float(agg["min_score"] or 0.0),
                        "max_score": float(agg["max_score"] or 0.0),
                        "pass_count": int(pcount),
                        "fail_count": int(fcount),
                        "pass_rate": round(float(p_rate), 4),
                    }
                )

        rows.append(
            {
                "session_id": int(s.id),
                "order": _safe_int(getattr(s, "order", None), 0) if getattr(s, "order", None) is not None else None,
                "participant_count": int(participant_count),
                "pass_rate": round(float(pass_rate), 4),
                "clinic_rate": round(float(clinic_rate), 4),
                "exams": ex_rows,
            }
        )

    return {
        "lecture_id": int(lecture.id),
        "strategy": str(meta["strategy"]),
        "pass_source": str(meta["pass_source"]),
        "session_count": int(sessions_qs.count()),
        "sessions": rows,
        "generated_at": timezone.now().isoformat(),
    }
