"""Student-app result read models."""

from __future__ import annotations

from math import isfinite
from typing import Any

from django.db.models import F, Max

from apps.domains.enrollment.selectors import active_enrollment_ids_for_student
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.progress.models import ClinicLink
from apps.domains.results.services.student_result_service import get_my_exam_result_data
from apps.domains.results.utils.ranking import compute_exam_rankings_batch
from apps.support.results.student_grade_history import (
    build_student_exam_history,
    empty_exam_summary,
)


def get_student_exam_result_data(request: Any, exam_id: int, *, tenant: Any):
    return get_my_exam_result_data(request, exam_id, tenant=tenant)


def _empty_result_item_analysis():
    return {
        "total_questions": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "accuracy_rate": None,
        "wrong_question_numbers": [],
    }


def _summarize_grade_result_items(result, *, structure_exam_id: int):
    total = 0
    correct = 0
    wrong_numbers = []

    for item in result.items.all():
        question = getattr(item, "question", None)
        sheet = getattr(question, "sheet", None) if question else None
        if (
            not sheet
            or int(getattr(sheet, "exam_id", 0) or 0) != int(structure_exam_id)
        ):
            continue
        total += 1
        if item.is_correct:
            correct += 1
            continue

        raw_number = getattr(question, "number", None)
        try:
            wrong_numbers.append(int(raw_number))
        except (TypeError, ValueError):
            continue

    wrong_numbers.sort()
    return {
        "total_questions": total,
        "correct_count": correct,
        "wrong_count": max(total - correct, 0),
        "accuracy_rate": round((correct / total) * 100, 1) if total else None,
        "wrong_question_numbers": wrong_numbers,
    }


def _session_titles(session: Any) -> tuple[str | None, str | None]:
    if not session:
        return None, None
    session_title = getattr(session, "title", None) or getattr(session, "display_label", "")
    lecture = getattr(session, "lecture", None)
    lecture_title = getattr(lecture, "title", None) if lecture else None
    return session_title, lecture_title


def _default_homework_max_score(homework: Any) -> float | None:
    meta = getattr(homework, "meta", None) or {}
    if not isinstance(meta, dict):
        return None
    default_max_score = meta.get("default_max_score")
    if default_max_score is None:
        return None
    try:
        value = float(default_max_score)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def _safe_homework_number(value: Any, *, positive: bool = False) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    minimum_ok = parsed > 0 if positive else parsed >= 0
    return parsed if isfinite(parsed) and minimum_ok else None


def build_student_grades_summary(*, tenant: Any, student: Any) -> dict[str, Any]:
    enrollment_ids = active_enrollment_ids_for_student(
        tenant=tenant,
        student=student,
    )
    if not enrollment_ids:
        return {
            "exams": [],
            "homeworks": [],
            "exam_trend": [],
            "exam_summary": empty_exam_summary(),
        }

    exam_list, exam_trend, exam_summary = build_student_exam_history(
        tenant=tenant,
        enrollment_ids=enrollment_ids,
    )
    exam_ids = [int(exam["exam_id"]) for exam in exam_list]
    result_ids = [int(exam["_result_id"]) for exam in exam_list]
    structure_exam_id_by_result_id = {
        int(exam["_result_id"]): int(exam["_structure_exam_id"])
        for exam in exam_list
    }
    result_analysis_map = {}
    if result_ids:
        from apps.domains.results.models import Result

        result_rows = (
            Result.objects
            .filter(id__in=result_ids)
            .prefetch_related("items__question__sheet")
        )
        result_analysis_map = {
            int(result.id): _summarize_grade_result_items(
                result,
                structure_exam_id=structure_exam_id_by_result_id[int(result.id)],
            )
            for result in result_rows
        }

    exam_rank_maps = compute_exam_rankings_batch(
        exam_ids=exam_ids,
        enrollment_ids=enrollment_ids,
        tenant=tenant,
    )

    for exam in exam_list:
        result_id = int(exam.pop("_result_id"))
        exam.pop("_structure_exam_id")
        exam_id = int(exam["exam_id"])
        enrollment_id = int(exam["enrollment_id"])
        rank_info = exam_rank_maps.get(exam_id, {}).get(enrollment_id, {})
        item_analysis = result_analysis_map.get(result_id) or _empty_result_item_analysis()
        exam.update({
            "rank": rank_info.get("rank"),
            "percentile": rank_info.get("percentile"),
            "cohort_size": rank_info.get("cohort_size"),
            "cohort_avg": rank_info.get("cohort_avg"),
            "total_questions": item_analysis["total_questions"],
            "correct_count": item_analysis["correct_count"],
            "wrong_count": item_analysis["wrong_count"],
            "accuracy_rate": item_analysis["accuracy_rate"],
            "wrong_question_numbers": item_analysis["wrong_question_numbers"],
        })

    homework_scores = (
        HomeworkScore.objects.filter(
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            homework__tenant=tenant,
            session__lecture__tenant=tenant,
            session__lecture_id=F("enrollment__lecture_id"),
            homework__session_id=F("session_id"),
            attempt_index=1,
        )
        .exclude(score__isnull=True)
        .exclude(session__lecture__is_system=True)
        .select_related("homework", "session", "session__lecture")
        .order_by("-updated_at")
    )
    homework_ids = list({score.homework_id for score in homework_scores})
    resolved_homework_links = {}
    if homework_ids and enrollment_ids:
        for link in ClinicLink.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            source_type="homework",
            source_id__in=homework_ids,
            resolved_at__isnull=False,
            resolution_type__in=["EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"],
        ).values("enrollment_id", "source_id", "resolution_type"):
            resolved_homework_links[(link["enrollment_id"], link["source_id"])] = link["resolution_type"]

    homework_retake_counts = {}
    if homework_ids and enrollment_ids:
        for row in (
            HomeworkScore.objects
            .filter(
                homework_id__in=homework_ids,
                enrollment_id__in=enrollment_ids,
                enrollment__tenant=tenant,
                homework__tenant=tenant,
                session__lecture__tenant=tenant,
                session__lecture_id=F("enrollment__lecture_id"),
                homework__session_id=F("session_id"),
            )
            .values("homework_id", "enrollment_id")
            .annotate(max_attempt=Max("attempt_index"))
        ):
            homework_retake_counts[(row["enrollment_id"], row["homework_id"])] = row["max_attempt"]

    homework_list = []
    seen_homework_key = set()
    for score in homework_scores:
        safe_score = _safe_homework_number(score.score)
        if safe_score is None:
            continue
        key = (score.homework_id, score.session_id, score.enrollment_id)
        if key in seen_homework_key:
            continue
        seen_homework_key.add(key)
        session_title, lecture_title = _session_titles(score.session)

        is_pass_1st = bool(score.passed)
        resolution = resolved_homework_links.get((score.enrollment_id, score.homework_id))
        max_attempt = homework_retake_counts.get((score.enrollment_id, score.homework_id), 1)

        if is_pass_1st:
            achievement = "PASS"
        elif resolution in ("EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"):
            achievement = "REMEDIATED"
        else:
            achievement = "FAIL"

        effective_max = _safe_homework_number(score.max_score, positive=True)
        if effective_max is None and score.homework:
            effective_max = _default_homework_max_score(score.homework)

        homework_list.append({
            "homework_id": score.homework_id,
            "enrollment_id": score.enrollment_id,
            "title": score.homework.title if score.homework else f"과제 #{score.homework_id}",
            "score": safe_score,
            "max_score": effective_max,
            "passed": is_pass_1st,
            "achievement": achievement,
            "retake_count": max_attempt,
            "session_title": session_title,
            "lecture_title": lecture_title,
            "recorded_at": score.updated_at.isoformat(),
        })

    assigned_homeworks = (
        HomeworkAssignment.objects
        .filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            homework__tenant=tenant,
            session__lecture__tenant=tenant,
            session__lecture_id=F("enrollment__lecture_id"),
            homework__session_id=F("session_id"),
        )
        .exclude(homework__meta__removed_from_session_at__isnull=False)
        .exclude(session__lecture__is_system=True)
        .select_related("homework", "session", "session__lecture")
        .order_by("-homework__updated_at", "-homework_id")
    )
    for assignment in assigned_homeworks:
        homework = assignment.homework
        session = assignment.session
        key = (assignment.homework_id, assignment.session_id, assignment.enrollment_id)
        if key in seen_homework_key:
            continue
        seen_homework_key.add(key)

        effective_max = _default_homework_max_score(homework)
        assignment_session_title, assignment_lecture_title = _session_titles(session)

        homework_list.append({
            "homework_id": assignment.homework_id,
            "enrollment_id": assignment.enrollment_id,
            "title": homework.title if homework else f"과제 #{assignment.homework_id}",
            "score": None,
            "max_score": effective_max,
            "passed": False,
            "achievement": "NOT_SUBMITTED",
            "retake_count": 0,
            "session_title": assignment_session_title,
            "lecture_title": assignment_lecture_title,
            "recorded_at": assignment.created_at.isoformat(),
        })

    return {
        "exams": exam_list,
        "homeworks": homework_list,
        "exam_trend": exam_trend,
        "exam_summary": exam_summary,
        "labels": {
            "pass": (getattr(tenant, "pass_label", None) or "").strip(),
            "fail": (getattr(tenant, "fail_label", None) or "").strip(),
        },
    }
