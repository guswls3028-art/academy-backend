from __future__ import annotations

from apps.domains.exams.models import Exam, ExamLecturePolicy


def effective_pass_scores_for_exam(
    *,
    exam: Exam,
    lecture_ids: set[int],
) -> dict[int, float]:
    overrides = {
        int(row["lecture_id"]): float(row["pass_score"])
        for row in ExamLecturePolicy.objects.filter(
            exam=exam,
            lecture_id__in=lecture_ids,
        ).values("lecture_id", "pass_score")
    }
    default_score = float(exam.pass_score or 0.0)
    return {
        lecture_id: overrides.get(lecture_id, default_score)
        for lecture_id in lecture_ids
    }


def effective_pass_score_for_exam(*, exam: Exam, lecture_id: int | None) -> float:
    if lecture_id is None:
        return float(exam.pass_score or 0.0)
    return effective_pass_scores_for_exam(
        exam=exam,
        lecture_ids={int(lecture_id)},
    )[int(lecture_id)]
