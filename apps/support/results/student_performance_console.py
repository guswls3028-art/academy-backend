"""Tenant-scoped read model for the staff student performance console."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from math import isfinite
from statistics import fmean
from typing import Any

from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture
from apps.domains.results.models import Result
from apps.domains.students.models import Student


PERFORMANCE_DAY_OPTIONS = (30, 90, 180, 365)


def performance_lecture_exists(*, tenant: Any, lecture_id: int) -> bool:
    return Lecture.objects.filter(
        tenant=tenant,
        id=lecture_id,
        is_system=False,
    ).exists()


def normalize_performance_days(raw: Any, *, default: int = 180) -> int | None:
    """Accept the console's bounded period options or the explicit all-time value."""
    if isinstance(raw, str) and raw.strip().lower() == "all":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value in PERFORMANCE_DAY_OPTIONS else default


def _score_pct(score: Any, max_score: Any) -> float | None:
    try:
        score_value = float(score)
        max_score_value = float(max_score)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not isfinite(score_value)
        or not isfinite(max_score_value)
        or score_value < 0
        or max_score_value <= 0
    ):
        return None
    value = (score_value / max_score_value) * 100
    return round(value, 1) if isfinite(value) else None


def _school_name(row: dict[str, Any]) -> str | None:
    school_type = row.get("school_type")
    if school_type == "ELEMENTARY":
        return row.get("elementary_school")
    if school_type == "MIDDLE":
        return row.get("middle_school")
    return row.get("high_school")


def _score_band(latest: float | None) -> str:
    if latest is None:
        return "unscored"
    if latest < 60:
        return "under_60"
    if latest < 80:
        return "60_to_79"
    return "80_plus"


def _trend_direction(change: float | None) -> str:
    if change is None:
        return "insufficient"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _display_names(student_rows: list[dict[str, Any]]) -> dict[int, str]:
    name_counts = Counter(str(row.get("name") or "") for row in student_rows)
    name_indexes: defaultdict[str, int] = defaultdict(int)
    output = {}
    for row in sorted(student_rows, key=lambda item: int(item["id"])):
        name = str(row.get("name") or "학생")
        if name_counts[name] > 1:
            name_indexes[name] += 1
            output[int(row["id"])] = f"{name}{name_indexes[name]}"
        else:
            output[int(row["id"])] = name
    return output


def build_student_performance_console(
    *,
    tenant: Any,
    days: int | None = 180,
    lecture_id: int | None = None,
) -> dict[str, Any]:
    """Build all roster summaries in a fixed number of tenant-scoped queries."""
    student_rows = list(
        Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            is_managed=True,
        ).values(
            "id",
            "name",
            "grade",
            "school_type",
            "elementary_school",
            "middle_school",
            "high_school",
        )
    )
    all_student_ids = {int(row["id"]) for row in student_rows}
    display_names = _display_names(student_rows)

    enrollment_rows = list(
        Enrollment.objects.filter(
            tenant=tenant,
            student_id__in=all_student_ids,
            lecture__tenant=tenant,
            lecture__is_system=False,
        ).values(
            "id",
            "student_id",
            "status",
            "lecture_id",
            "lecture__title",
            "lecture__color",
            "lecture__chip_label",
            "lecture__is_active",
        )
    )

    lecture_options_by_id: dict[int, dict[str, Any]] = {}
    lectures_by_student: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    enrollment_student_lecture: dict[int, tuple[int, int]] = {}
    selected_enrollment_ids: set[int] = set()
    lecture_student_ids: set[int] = set()
    for enrollment in enrollment_rows:
        enrollment_id = int(enrollment["id"])
        student_id = int(enrollment["student_id"])
        row_lecture_id = int(enrollment["lecture_id"])
        enrollment_student_lecture[enrollment_id] = (student_id, row_lecture_id)
        lecture = {
            "id": row_lecture_id,
            "title": enrollment["lecture__title"],
            "color": enrollment["lecture__color"],
            "chip_label": enrollment["lecture__chip_label"],
            "is_active": bool(enrollment["lecture__is_active"]),
        }
        lecture_options_by_id.setdefault(row_lecture_id, lecture)
        lectures_by_student[student_id].append({
            **lecture,
            "enrollment_id": enrollment_id,
            "enrollment_status": enrollment["status"],
        })
        if lecture_id is None or row_lecture_id == lecture_id:
            selected_enrollment_ids.add(enrollment_id)
        if lecture_id is not None and row_lecture_id == lecture_id:
            lecture_student_ids.add(student_id)

    if lecture_id is not None:
        student_rows = [row for row in student_rows if int(row["id"]) in lecture_student_ids]
    selected_student_ids = {int(row["id"]) for row in student_rows}
    selected_enrollment_ids = {
        enrollment_id
        for enrollment_id in selected_enrollment_ids
        if enrollment_student_lecture[enrollment_id][0] in selected_student_ids
    }

    exam_rows = Exam.objects.filter(
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
    ).values("id", "title")
    exam_titles = {int(row["id"]): row["title"] for row in exam_rows}

    result_query = (
        Result.objects.filter(
            target_type="exam",
            target_id__in=exam_titles,
            enrollment_id__in=selected_enrollment_ids,
            enrollment__tenant=tenant,
            enrollment__student_id__in=selected_student_ids,
        )
        .annotate(recorded_at=Coalesce("submitted_at", "created_at"))
        .order_by("recorded_at", "id")
        .values(
            "id",
            "target_id",
            "enrollment_id",
            "total_score",
            "max_score",
            "recorded_at",
            "attempt__meta",
        )
    )
    if days is not None:
        result_query = result_query.filter(recorded_at__gte=timezone.now() - timedelta(days=days))

    latest_result_by_exam: dict[tuple[int, int], dict[str, Any]] = {}
    for result in result_query:
        enrollment_meta = enrollment_student_lecture.get(int(result["enrollment_id"]))
        if not enrollment_meta:
            continue
        student_id, row_lecture_id = enrollment_meta
        attempt_meta = result.get("attempt__meta")
        if isinstance(attempt_meta, dict) and attempt_meta.get("status") == "NOT_SUBMITTED":
            continue
        score_pct = _score_pct(result.get("total_score"), result.get("max_score"))
        if score_pct is None:
            continue
        latest_result_by_exam[(student_id, int(result["target_id"]))] = {
            "exam_id": int(result["target_id"]),
            "lecture_id": row_lecture_id,
            "title": exam_titles[int(result["target_id"])],
            "score_pct": score_pct,
            "recorded_at": result["recorded_at"],
        }

    results_by_student: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for (student_id, _exam_id), result in latest_result_by_exam.items():
        results_by_student[student_id].append(result)

    output_students = []
    all_score_values = []
    for student in student_rows:
        student_id = int(student["id"])
        points = sorted(
            results_by_student.get(student_id, []),
            key=lambda item: (item["recorded_at"], item["exam_id"]),
        )
        values = [float(point["score_pct"]) for point in points]
        all_score_values.extend(values)
        latest = values[-1] if values else None
        previous = values[-2] if len(values) > 1 else None
        change = round(latest - previous, 1) if latest is not None and previous is not None else None
        first_to_latest = (
            round(latest - values[0], 1)
            if latest is not None and len(values) > 1
            else None
        )
        student_lectures = sorted(
            lectures_by_student.get(student_id, []),
            key=lambda item: (not item["is_active"], item["title"], item["id"]),
        )
        output_students.append({
            "student_id": student_id,
            "name": student["name"],
            "display_name": display_names[student_id],
            "grade": student["grade"],
            "school_type": student["school_type"],
            "school": _school_name(student),
            "lectures": student_lectures,
            "scored_count": len(values),
            "average_score_pct": round(fmean(values), 1) if values else None,
            "latest_score_pct": latest,
            "change_pct_points": change,
            "first_to_latest_pct_points": first_to_latest,
            "best_score_pct": max(values) if values else None,
            "latest_exam_title": points[-1]["title"] if points else None,
            "last_recorded_at": points[-1]["recorded_at"].isoformat() if points else None,
            "score_band": _score_band(latest),
            "trend_direction": _trend_direction(change),
        })

    score_band_order = {"under_60": 0, "60_to_79": 1, "80_plus": 2, "unscored": 3}
    output_students.sort(
        key=lambda row: (
            score_band_order[row["score_band"]],
            row["latest_score_pct"] if row["latest_score_pct"] is not None else 10**9,
            row["display_name"],
        )
    )
    scored_students = [row for row in output_students if row["scored_count"] > 0]
    return {
        "period": {
            "days": days,
            "from": (timezone.now() - timedelta(days=days)).date().isoformat() if days is not None else None,
            "to": timezone.now().date().isoformat(),
        },
        "summary": {
            "student_count": len(output_students),
            "scored_student_count": len(scored_students),
            "result_count": len(all_score_values),
            "average_score_pct": round(fmean(all_score_values), 1) if all_score_values else None,
            "under_60_student_count": sum(1 for row in output_students if row["score_band"] == "under_60"),
            "improving_student_count": sum(1 for row in output_students if row["trend_direction"] == "up"),
            "declining_student_count": sum(1 for row in output_students if row["trend_direction"] == "down"),
        },
        "filter_options": {
            "lectures": sorted(
                lecture_options_by_id.values(),
                key=lambda item: (not item["is_active"], item["title"], item["id"]),
            ),
            "grades": sorted({int(row["grade"]) for row in student_rows if row.get("grade") is not None}),
        },
        "students": output_students,
    }
