from __future__ import annotations

from typing import Any

from apps.domains.results.services.answer_matching import format_answer_for_display
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)
from apps.support.results.wrong_note_dependencies import (
    answer_key_map_for_effective_exam,
    explanation_image_key,
    explanation_image_url,
    get_selected_workbook,
    get_selected_workbook_score,
    question_image_key,
    question_image_url,
    selected_regular_exam_exists,
    selected_regular_exams_for_lecture,
    selected_source_enrollments,
    selected_workbook_assignment_exists,
    selected_workbook_assignments_for_enrollment,
    selected_workbook_source_is_ready,
)


class WrongNoteSourceSelectionError(ValueError):
    pass


def _enrollments_for_student(*, tenant_id: int, student_id: int):
    return selected_source_enrollments(
        tenant_id=tenant_id,
        student_id=student_id,
    )


def normalize_wrong_note_source_selection(
    *,
    tenant_id: int,
    student_id: int,
    raw_sources: Any,
) -> list[dict[str, int | str]]:
    if not isinstance(raw_sources, list) or not raw_sources:
        raise WrongNoteSourceSelectionError("시험 또는 워크북을 하나 이상 선택해 주세요.")
    if len(raw_sources) > 100:
        raise WrongNoteSourceSelectionError("한 번에 100개 자료까지만 선택할 수 있습니다.")

    enrollments = {
        int(item.id): item
        for item in _enrollments_for_student(
            tenant_id=tenant_id,
            student_id=student_id,
        )
    }
    if not enrollments:
        raise WrongNoteSourceSelectionError("학생의 수강 정보를 찾을 수 없습니다.")

    normalized: list[dict[str, int | str]] = []
    seen: set[tuple[str, int, int]] = set()
    for raw in raw_sources:
        try:
            source_type = str(raw.get("type") or "").strip().lower()
            source_id = int(raw.get("id"))
            enrollment_id = int(raw.get("enrollment_id"))
        except (AttributeError, TypeError, ValueError):
            raise WrongNoteSourceSelectionError("선택한 자료 정보를 다시 확인해 주세요.")
        if source_type not in {"exam", "homework"} or source_id < 1:
            raise WrongNoteSourceSelectionError("시험 또는 워크북 자료만 선택할 수 있습니다.")
        enrollment = enrollments.get(enrollment_id)
        if enrollment is None:
            raise WrongNoteSourceSelectionError("다른 학생의 자료는 함께 만들 수 없습니다.")
        dedupe_key = (source_type, source_id, enrollment_id)
        if dedupe_key in seen:
            raise WrongNoteSourceSelectionError("같은 자료가 중복 선택되었습니다.")
        seen.add(dedupe_key)

        if source_type == "exam":
            allowed = selected_regular_exam_exists(
                exam_id=source_id,
                tenant_id=tenant_id,
                lecture_id=int(enrollment.lecture_id),
            )
        else:
            allowed = selected_workbook_assignment_exists(
                tenant_id=tenant_id,
                enrollment_id=enrollment_id,
                homework_id=source_id,
                lecture_id=int(enrollment.lecture_id),
            )
        if not allowed:
            raise WrongNoteSourceSelectionError("현재 학생에게 배정된 자료만 선택할 수 있습니다.")
        normalized.append(
            {
                "type": source_type,
                "id": source_id,
                "enrollment_id": enrollment_id,
            }
        )
    return normalized


def _homework_wrong_note_items(
    *,
    tenant_id: int,
    enrollment_id: int,
    homework_id: int,
) -> list[dict[str, Any]]:
    homework = get_selected_workbook(
        tenant_id=tenant_id,
        homework_id=homework_id,
    )
    if homework is None or homework.source_exam is None:
        return []
    score = get_selected_workbook_score(
        enrollment_id=enrollment_id,
        homework=homework,
    )
    raw_marks = (getattr(score, "meta", None) or {}).get("question_marks")
    marks = dict(raw_marks) if isinstance(raw_marks, dict) else {}
    if not marks:
        return []

    questions = {
        int(item.number): item
        for item in homework.source_exam.sheet.questions.select_related(
            "explanation"
        ).order_by("number", "id")
    }
    answer_keys = answer_key_map_for_effective_exam(
        exam_id=int(homework.source_exam_id),
        tenant_id=int(tenant_id),
    )
    items: list[dict[str, Any]] = []
    for raw_number, raw_mark in marks.items():
        try:
            number = int(raw_number)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_mark, dict):
            continue
        is_correct = raw_mark.get("is_correct")
        include = raw_mark.get("include_in_wrong_note") is True
        if is_correct is not False and not include:
            continue
        question = questions.get(number)
        if question is None:
            continue
        try:
            explanation_text = str(question.explanation.text or "")
        except Exception:
            explanation_text = ""
        problem_key = question_image_key(question=question, tenant_id=tenant_id)
        solution_key = explanation_image_key(question=question, tenant_id=tenant_id)
        items.append(
            {
                "source_type": "homework",
                "source_id": int(homework.id),
                "enrollment_id": int(enrollment_id),
                "exam_id": int(homework.source_exam_id),
                "exam_title": str(homework.title or "워크북"),
                "session_order": getattr(homework.session, "regular_order", None),
                "session_title": str(getattr(homework.session, "title", "") or ""),
                "attempt_id": int(score.id) if score else 0,
                "attempt_created_at": getattr(score, "updated_at", None),
                "question_id": int(question.id),
                "question_number": int(question.number),
                "answer_type": str(question.question_kind or ""),
                "question_image_url": question_image_url(
                    question=question,
                    tenant_id=tenant_id,
                ),
                "has_question_image": bool(problem_key or question.image),
                "explanation_image_url": explanation_image_url(
                    question=question,
                    tenant_id=tenant_id,
                ),
                "has_teacher_explanation": bool(explanation_text or solution_key),
                "student_answer": "",
                "correct_answer": format_answer_for_display(
                    answer_keys.get(str(question.id)) or ""
                ),
                "is_correct": bool(is_correct),
                "include_in_wrong_note": include,
                "score": 0.0,
                "max_score": float(question.score or 0.0),
                "meta": {"source_type": "homework", "homework_id": int(homework.id)},
                "extra": {"explanation_text": explanation_text},
                "_question_image_key": problem_key,
                "_question_image_name": str(getattr(question.image, "name", "") or ""),
                "_explanation_image_key": solution_key,
            }
        )
    return sorted(items, key=lambda item: (int(item["question_number"]), int(item["question_id"])))


def list_wrong_notes_for_selection(
    *,
    tenant_id: int,
    student_id: int,
    source_selection: Any,
    limit: int = 200,
) -> tuple[int, list[dict[str, Any]], list[dict[str, int | str]]]:
    normalized = normalize_wrong_note_source_selection(
        tenant_id=tenant_id,
        student_id=student_id,
        raw_sources=source_selection,
    )
    lecture_by_enrollment = dict(
        _enrollments_for_student(
            tenant_id=tenant_id,
            student_id=student_id,
        )
        .filter(id__in=[int(source["enrollment_id"]) for source in normalized])
        .values_list("id", "lecture_id")
    )
    merged: list[dict[str, Any]] = []
    for source in normalized:
        if source["type"] == "exam":
            _, items = list_wrong_notes_for_enrollment(
                enrollment_id=int(source["enrollment_id"]),
                q=WrongNoteQuery(
                    exam_id=int(source["id"]),
                    lecture_id=int(
                        lecture_by_enrollment[int(source["enrollment_id"])]
                    ),
                    offset=0,
                    limit=200,
                ),
            )
            for item in items:
                item["source_type"] = "exam"
                item["source_id"] = int(source["id"])
                item["enrollment_id"] = int(source["enrollment_id"])
            merged.extend(items)
        else:
            merged.extend(
                _homework_wrong_note_items(
                    tenant_id=tenant_id,
                    enrollment_id=int(source["enrollment_id"]),
                    homework_id=int(source["id"]),
                )
            )
    total = len(merged)
    return total, merged[: max(min(int(limit), 200), 1)], normalized


def list_wrong_note_sources_for_student(*, tenant_id: int, student_id: int) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    enrollments = list(
        _enrollments_for_student(tenant_id=tenant_id, student_id=student_id)
        .order_by("lecture__title", "id")
    )
    for enrollment in enrollments:
        exams = selected_regular_exams_for_lecture(
            tenant_id=tenant_id,
            lecture_id=int(enrollment.lecture_id),
        )
        for exam in exams:
            total, _ = list_wrong_notes_for_enrollment(
                enrollment_id=int(enrollment.id),
                q=WrongNoteQuery(exam_id=int(exam.id), offset=0, limit=1),
            )
            sources.append(
                {
                    "type": "exam",
                    "id": int(exam.id),
                    "enrollment_id": int(enrollment.id),
                    "lecture_id": int(enrollment.lecture_id),
                    "lecture_title": str(enrollment.lecture.title or ""),
                    "title": str(exam.title or "시험"),
                    "session_order": None,
                    "wrong_note_count": int(total),
                    "ready": True,
                }
            )

        assignments = selected_workbook_assignments_for_enrollment(
            tenant_id=tenant_id,
            enrollment=enrollment,
        )
        for assignment in assignments:
            homework = assignment.homework
            items = _homework_wrong_note_items(
                tenant_id=tenant_id,
                enrollment_id=int(enrollment.id),
                homework_id=int(homework.id),
            )
            sources.append(
                {
                    "type": "homework",
                    "id": int(homework.id),
                    "enrollment_id": int(enrollment.id),
                    "lecture_id": int(enrollment.lecture_id),
                    "lecture_title": str(enrollment.lecture.title or ""),
                    "title": str(homework.title or "워크북"),
                    "session_order": getattr(homework.session, "regular_order", None),
                    "wrong_note_count": len(items),
                    "ready": selected_workbook_source_is_ready(
                        homework.source_exam
                    ),
                }
            )
    return sources
