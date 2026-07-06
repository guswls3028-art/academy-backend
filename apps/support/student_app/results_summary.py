"""Student-app result read models."""

from __future__ import annotations

from typing import Any

from django.db.models import Max

from apps.domains.enrollment.selectors import active_enrollment_ids_for_student
from apps.domains.exams.models import Exam
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.progress.models import ClinicLink
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.services.student_result_service import get_my_exam_result_data
from apps.domains.results.utils.ranking import compute_exam_rankings_batch
from apps.domains.results.utils.session_exam import get_primary_session_for_exam


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


def _summarize_grade_result_items(result):
    total = 0
    correct = 0
    wrong_numbers = []

    for item in result.items.all():
        total += 1
        if item.is_correct:
            correct += 1
            continue

        question = getattr(item, "question", None)
        raw_number = getattr(question, "number", None) or item.question_id
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
    return value if value > 0 else None


def build_student_grades_summary(*, tenant: Any, student: Any) -> dict[str, Any]:
    enrollment_ids = active_enrollment_ids_for_student(
        tenant=tenant,
        student=student,
    )
    if not enrollment_ids:
        return {"exams": [], "homeworks": []}

    results = list(
        Result.objects.filter(
            enrollment_id__in=enrollment_ids,
            target_type="exam",
        )
        .order_by("-submitted_at")
        .values(
            "id",
            "target_id",
            "enrollment_id",
            "total_score",
            "max_score",
            "submitted_at",
            "attempt_id",
        )
    )
    exam_ids = list({r["target_id"] for r in results})
    result_ids = [int(r["id"]) for r in results if r.get("id")]
    result_analysis_map = {}
    if result_ids:
        result_rows = (
            Result.objects
            .filter(id__in=result_ids)
            .prefetch_related("items__question")
        )
        result_analysis_map = {
            int(result.id): _summarize_grade_result_items(result)
            for result in result_rows
        }

    attempt_ids = {int(r["attempt_id"]) for r in results if r.get("attempt_id")}
    attempt_meta_map = {}
    if attempt_ids:
        for attempt in ExamAttempt.objects.filter(id__in=attempt_ids).only("id", "meta"):
            attempt_meta_map[int(attempt.id)] = (attempt.meta or {}).get("status")

    exams_map = {}
    if exam_ids:
        for exam in Exam.objects.filter(id__in=exam_ids).only("id", "title", "pass_score"):
            exams_map[exam.id] = {
                "title": exam.title,
                "pass_score": float(exam.pass_score or 0),
            }

    resolved_exam_links = {}
    if exam_ids and enrollment_ids:
        for link in ClinicLink.objects.filter(
            enrollment_id__in=enrollment_ids,
            source_type="exam",
            source_id__in=exam_ids,
            resolved_at__isnull=False,
            resolution_type__in=["EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"],
        ).values("enrollment_id", "source_id", "resolution_type"):
            resolved_exam_links[(link["enrollment_id"], link["source_id"])] = link["resolution_type"]

    retake_counts = {}
    if exam_ids and enrollment_ids:
        for attempt in (
            ExamAttempt.objects
            .filter(exam_id__in=exam_ids, enrollment_id__in=enrollment_ids)
            .values("exam_id", "enrollment_id")
            .annotate(max_attempt=Max("attempt_index"))
        ):
            retake_counts[(attempt["enrollment_id"], attempt["exam_id"])] = attempt["max_attempt"]

    exam_rank_maps = compute_exam_rankings_batch(
        exam_ids=exam_ids,
        enrollment_ids=enrollment_ids,
    )

    exam_list = []
    seen_exam_ids = set()
    for result in results:
        exam_id = result["target_id"]
        if exam_id in seen_exam_ids:
            continue
        seen_exam_ids.add(exam_id)

        info = exams_map.get(exam_id) or {"title": f"시험 #{exam_id}", "pass_score": 0}
        session = get_primary_session_for_exam(exam_id)
        if (
            session
            and getattr(session, "lecture", None)
            and getattr(session.lecture, "is_system", False)
        ):
            continue
        session_title, lecture_title = _session_titles(session)

        meta_status = (
            attempt_meta_map.get(int(result["attempt_id"]))
            if result.get("attempt_id")
            else None
        )
        is_not_submitted = meta_status == "NOT_SUBMITTED"
        raw_pass_score = info["pass_score"] or 0
        if is_not_submitted:
            is_pass_1st = None
        elif raw_pass_score > 0:
            is_pass_1st = float(result["total_score"]) >= raw_pass_score
        else:
            is_pass_1st = None

        enrollment_id = result["enrollment_id"]
        resolution = resolved_exam_links.get((enrollment_id, exam_id))
        max_attempt = retake_counts.get((enrollment_id, exam_id), 1)

        if is_not_submitted:
            achievement = "NOT_SUBMITTED"
        elif is_pass_1st is None:
            achievement = None
        elif is_pass_1st:
            achievement = "PASS"
        elif resolution in ("EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"):
            achievement = "REMEDIATED"
        else:
            achievement = "FAIL"

        rank_info = exam_rank_maps.get(exam_id, {}).get(enrollment_id, {})
        item_analysis = result_analysis_map.get(int(result["id"])) or _empty_result_item_analysis()

        exam_list.append({
            "exam_id": exam_id,
            "enrollment_id": enrollment_id,
            "title": info["title"],
            "total_score": None if is_not_submitted else result["total_score"],
            "max_score": result["max_score"],
            "is_pass": is_pass_1st,
            "achievement": achievement,
            "meta_status": meta_status,
            "retake_count": max_attempt,
            "session_title": session_title,
            "lecture_title": lecture_title,
            "submitted_at": result["submitted_at"].isoformat() if result.get("submitted_at") else None,
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
        HomeworkScore.objects.filter(enrollment_id__in=enrollment_ids, attempt_index=1)
        .exclude(score__isnull=True)
        .exclude(session__lecture__is_system=True)
        .select_related("homework", "session", "session__lecture")
        .order_by("-updated_at")
    )
    homework_ids = list({score.homework_id for score in homework_scores})
    resolved_homework_links = {}
    if homework_ids and enrollment_ids:
        for link in ClinicLink.objects.filter(
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
            .filter(homework_id__in=homework_ids, enrollment_id__in=enrollment_ids)
            .values("homework_id", "enrollment_id")
            .annotate(max_attempt=Max("attempt_index"))
        ):
            homework_retake_counts[(row["enrollment_id"], row["homework_id"])] = row["max_attempt"]

    homework_list = []
    seen_homework_key = set()
    for score in homework_scores:
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

        effective_max = score.max_score
        if effective_max is None and score.homework:
            effective_max = _default_homework_max_score(score.homework)

        homework_list.append({
            "homework_id": score.homework_id,
            "enrollment_id": score.enrollment_id,
            "title": score.homework.title if score.homework else f"과제 #{score.homework_id}",
            "score": score.score,
            "max_score": effective_max,
            "passed": is_pass_1st,
            "achievement": achievement,
            "retake_count": max_attempt,
            "session_title": session_title,
            "lecture_title": lecture_title,
        })

    assigned_homeworks = (
        HomeworkAssignment.objects
        .filter(tenant=tenant, enrollment_id__in=enrollment_ids)
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
        })

    return {
        "exams": exam_list,
        "homeworks": homework_list,
        "labels": {
            "pass": (getattr(tenant, "pass_label", None) or "").strip(),
            "fail": (getattr(tenant, "fail_label", None) or "").strip(),
        },
    }
