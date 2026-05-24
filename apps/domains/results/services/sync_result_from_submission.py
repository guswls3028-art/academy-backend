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
from apps.domains.results.services.attempt_service import ExamAttemptService
from apps.domains.results.services.answer_matching import answer_matches
from apps.domains.results.services.submission_scope_guard import validate_exam_submission_scope


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
    enrollment = validate_exam_submission_scope(submission=submission, exam=exam)
    enrollment_id = int(enrollment.id)

    try:
        sheet, answer_key = GradingContractGuard.validate_exam_for_grading(exam)
    except Exception:
        return None

    key_map = {
        int(k): v
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
        is_correct = answer_matches(ans, correct_key)
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

    total = 0.0
    max_total = 0.0
    for item in items_payload:
        total += item["score"]
        max_total += item["max_score"]

    # 먼저 attempt 정책을 통과시킨다. 재응시 불가/최대 횟수 초과라면 Result를 건드리지 않는다.
    from apps.domains.results.models import ExamAttempt

    attempt = (
        ExamAttempt.objects
        .select_for_update()
        .filter(submission_id=submission.id)
        .first()
    )
    if attempt:
        attempt.status = "done"
        attempt.save(update_fields=["status", "updated_at"])
    else:
        attempt = ExamAttemptService.create_for_submission(
            exam_id=int(exam.id),
            enrollment_id=int(enrollment_id),
            submission_id=int(submission.id),
        )
        attempt.status = "done"
        if int(attempt.attempt_index) == 1:
            attempt.meta = {
                "initial_snapshot": {
                    "total_score": float(total),
                    "max_score": float(max_total),
                    "submitted_at": timezone.now().isoformat(),
                    "submission_id": int(submission.id),
                }
            }
            attempt.save(update_fields=["status", "meta", "updated_at"])
        else:
            attempt.save(update_fields=["status", "updated_at"])

    # 기존 Result가 있으면 이전 대표 attempt의 meta에 최종 snapshot 보존.
    # 재응시로 Result가 덮어쓰여도 이전 점수 이력이 손실되지 않음.
    existing_result_prev = Result.objects.filter(
        target_type="exam",
        target_id=int(exam.id),
        enrollment_id=int(enrollment_id),
    ).first()
    if existing_result_prev and existing_result_prev.attempt_id:
        from apps.domains.results.models import ExamAttempt as _EA
        prev_rep = (
            _EA.objects.select_for_update()
            .filter(id=int(existing_result_prev.attempt_id))
            .first()
        )
        if prev_rep:
            m = dict(prev_rep.meta or {}) if isinstance(prev_rep.meta, dict) else {}
            m["final_result_snapshot"] = {
                "total_score": existing_result_prev.total_score,
                "max_score": existing_result_prev.max_score,
                "objective_score": existing_result_prev.objective_score,
                "submitted_at": (
                    existing_result_prev.submitted_at.isoformat()
                    if existing_result_prev.submitted_at else None
                ),
                "archived_at": timezone.now().isoformat(),
                "replaced_by_submission": int(submission.id),
            }
            prev_rep.meta = m
            prev_rep.save(update_fields=["meta", "updated_at"])

    # ✅ Legacy backfill: 기존 attempt_index=1에 initial_snapshot이 없으면
    # 재응시로 Result가 덮어써지기 전의 현재 Result 값으로 1회 backfill.
    # 관리자 수동 점수 입력 경로는 initial_snapshot 저장을 추가했지만,
    # 이 패치 이전 생성된 attempt_index=1은 snapshot이 없어 ranking이
    # Result.total_score(=재응시로 덮여쓸 값)로 fallback되어 "석차=1차" 정책이 깨진다.
    # sync 호출 직전 Result가 1차 값이라는 보장은 없으나, 아직 덮여쓰이기 전
    # 마지막 기회이므로 "없는 것보다는 낫다" 원칙으로 현재 Result 값을 잠근다.
    if existing_result_prev:
        from apps.domains.results.models import ExamAttempt as _EA
        prev_idx1 = (
            _EA.objects.select_for_update()
            .filter(
                exam_id=int(exam.id),
                enrollment_id=int(enrollment_id),
                attempt_index=1,
            )
            .first()
        )
        if prev_idx1:
            m1 = dict(prev_idx1.meta or {}) if isinstance(prev_idx1.meta, dict) else {}
            if "initial_snapshot" not in m1:
                m1["initial_snapshot"] = {
                    "total_score": existing_result_prev.total_score,
                    "max_score": existing_result_prev.max_score,
                    "submitted_at": (
                        existing_result_prev.submitted_at.isoformat()
                        if existing_result_prev.submitted_at else None
                    ),
                    "source": "legacy_backfill",
                    "backfilled_at": timezone.now().isoformat(),
                }
                prev_idx1.meta = m1
                prev_idx1.save(update_fields=["meta", "updated_at"])

    result, _ = Result.objects.get_or_create(
        target_type="exam",
        target_id=int(exam.id),
        enrollment_id=int(enrollment_id),
        defaults={"total_score": 0, "max_score": 0},
    )

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
    result.total_score = total
    result.max_score = max_total
    result.objective_score = total  # 자동채점 문항 합산 = objective score
    result.submitted_at = timezone.now()
    result.save(update_fields=["total_score", "max_score", "objective_score", "submitted_at", "updated_at"])

    result.attempt_id = attempt.id
    result.save(update_fields=["attempt_id", "updated_at"])

    return result
