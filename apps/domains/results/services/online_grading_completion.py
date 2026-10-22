"""Read compatibility for fully automatic ONLINE results kept as legacy DRAFTs."""
from __future__ import annotations

from apps.domains.results.models import ExamResult
from apps.support.exams.numeric_short_answer import math_numeric_short_answer_question_ids
from apps.support.omr.score_shape import get_exam_score_shape
from apps.support.results.admin_exam_result_detail_dependencies import get_answer_key_answers
from apps.support.results.omr_subjective_completion_dependencies import exams_by_id


def completed_online_result_pairs(*, legacy_rows, completed_attempt_pairs, canonical_pairs, tenant_id):
    """Recognize completed automatic grading without rewriting historical scores.

    ONLINE has no separate teacher publication action. Its compatibility row
    historically stays DRAFT after the submission and canonical attempt finish.
    Require the same scoped completed attempt, canonical result, and a complete
    automatic-question snapshot. Human-graded essays and OMR retain their own
    finalization requirements. Exam-shape reads are shared by all students.
    """
    candidates = []
    for row in legacy_rows:
        pair = (row["submission_id"], row["submission__enrollment_id"], row["exam_id"])
        if (
            row["status"] == "DRAFT"
            and row["submission__source"] == "online"
            # A successful retake supersedes the completed first submission;
            # its immutable first-attempt score remains the total's source.
            and row["submission__status"] in {"done", "superseded"}
            and row["submission__target_id"] == row["exam_id"]
            and pair in completed_attempt_pairs
            and (pair[1], pair[2]) in canonical_pairs
        ):
            candidates.append(row)
    if not candidates:
        return set()

    automatic_questions = {}
    for exam_id, exam in exams_by_id(row["exam_id"] for row in candidates).items():
        if exam.tenant_id != tenant_id:
            continue
        shape = get_exam_score_shape(exam)
        question_ids = set(shape.question_kind_by_id)
        if (
            not question_ids or len(question_ids) != shape.total_questions
            or not set(shape.question_kind_by_id.values()).issubset({"choice", "essay"})
        ):
            continue
        essay_ids = {qid for qid, kind in shape.question_kind_by_id.items() if kind == "essay"}
        if essay_ids:
            numeric_ids = math_numeric_short_answer_question_ids(
                exam=exam, question_ids=question_ids, question_kind=shape.question_kind,
                answers=get_answer_key_answers(template_exam_id=shape.template_exam_id),
            )
            if not essay_ids.issubset(numeric_ids):
                continue
        automatic_questions[exam_id] = question_ids

    # Load answer snapshots only for scoped ONLINE candidates, never every
    # FINAL/OMR row in a large class. This remains one query for all students.
    breakdowns = dict(ExamResult.objects.filter(
        id__in=[row["id"] for row in candidates if row["exam_id"] in automatic_questions],
    ).values_list("id", "breakdown"))
    completed = set()
    for row in candidates:
        required = automatic_questions.get(row["exam_id"])
        breakdown = breakdowns.get(row["id"])
        if not required or not isinstance(breakdown, dict):
            continue
        entries = list(breakdown.values())
        if not all(isinstance(entry, dict) and type(entry.get("question_id")) is int for entry in entries):
            continue
        if len(entries) != len(required) or {entry["question_id"] for entry in entries} != required:
            continue
        completed.add((row["submission_id"], row["submission__enrollment_id"], row["exam_id"]))
    return completed
