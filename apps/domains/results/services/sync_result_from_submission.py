# PATH: apps/domains/results/services/sync_result_from_submission.py
"""
ONLINE 제출 채점 후 Result/ResultItem 동기화.
학생 결과 API(get_my_exam_result_data)가 Result를 사용하므로,
ExamResult만 있으면 404가 나는 문제를 해결하기 위해 호출.
"""
from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.results.models import Result, ResultItem
from apps.domains.results.guards.grading_contract import GradingContractGuard


@transaction.atomic
def sync_result_from_exam_submission(submission_id: int) -> Result | None:
    """
    Submission(ONLINE) 채점 완료 후 Result/ResultItem 생성·갱신.
    enrollment_id는 Submission에서 가져옴.
    """
    submission = get_object_or_404(
        Submission.objects.select_related("user"),
        id=int(submission_id),
    )
    if submission.target_type != "exam":
        return None

    from apps.domains.exams.models import Exam

    exam = get_object_or_404(Exam, id=int(submission.target_id))
    enrollment_id = getattr(submission, "enrollment_id", None)
    if not enrollment_id:
        return None

    try:
        sheet, answer_key = GradingContractGuard.validate_exam_for_grading(exam)
    except Exception:
        return None

    key_map = {
        int(k): str(v).strip()
        for k, v in (answer_key.answers or {}).items()
        if str(k).isdigit()
    }
    answers_map = {}
    for a in SubmissionAnswer.objects.filter(submission=submission):
        qid = int(getattr(a, "exam_question_id", 0) or 0)
        ans = str(getattr(a, "answer", "") or "").strip()
        if qid > 0:
            answers_map[qid] = ans

    questions = list(sheet.questions.all().order_by("number"))
    items_payload = []
    for q in questions:
        qid = int(q.id)
        ans = answers_map.get(qid, "")
        correct_key = key_map.get(qid, "")
        is_correct = bool(correct_key and ans == correct_key)
        max_score = float(getattr(q, "score", 0) or 0)
        score = max_score if is_correct else 0.0
        items_payload.append({
            "question_id": qid,
            "answer": ans,
            "is_correct": is_correct,
            "score": score,
            "max_score": max_score,
            "source": "online",
        })

    result, _ = Result.objects.get_or_create(
        target_type="exam",
        target_id=int(exam.id),
        enrollment_id=int(enrollment_id),
        defaults={"total_score": 0, "max_score": 0},
    )

    total = 0.0
    max_total = 0.0
    for item in items_payload:
        ResultItem.objects.update_or_create(
            result=result,
            question_id=item["question_id"],
            defaults={
                "answer": item["answer"],
                "is_correct": item["is_correct"],
                "score": item["score"],
                "max_score": item["max_score"],
                "source": item["source"],
            },
        )
        total += item["score"]
        max_total += item["max_score"]

    result.total_score = total
    result.max_score = max_total
    result.submitted_at = timezone.now()
    result.save(update_fields=["total_score", "max_score", "submitted_at", "updated_at"])
    return result
