"""Read compatibility for fully automatic ONLINE results kept as legacy DRAFTs."""
from __future__ import annotations

from apps.domains.results.models import ExamResult
from apps.support.exams.numeric_short_answer import numeric_short_answer_question_ids
from apps.support.omr.score_shape_batch import load_exam_scoring_details
from apps.support.results.omr_subjective_completion_dependencies import exams_by_id, math_exam_ids


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

    scoped_exams = {
        exam_id: exam
        for exam_id, exam in exams_by_id(row["exam_id"] for row in candidates).items()
        if exam.tenant_id == tenant_id
    }
    automatic_questions = {}
    essay_candidates = {}
    for exam_id, details in load_exam_scoring_details(scoped_exams.values()).items():
        shape = details.shape
        question_ids = set(shape.question_kind_by_id)
        if (
            not question_ids or len(question_ids) != shape.total_questions
            or not set(shape.question_kind_by_id.values()).issubset({"choice", "essay"})
        ):
            continue
        essay_ids = {qid for qid, kind in shape.question_kind_by_id.items() if kind == "essay"}
        if not essay_ids:
            automatic_questions[exam_id] = question_ids
        elif essay_ids == numeric_short_answer_question_ids(
            question_ids=question_ids, question_kind=shape.question_kind, answers=details.answers,
        ):
            essay_candidates[exam_id] = question_ids
    math_ids = math_exam_ids({exam_id: scoped_exams[exam_id] for exam_id in essay_candidates})
    automatic_questions.update({exam_id: essay_candidates[exam_id] for exam_id in math_ids})

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
