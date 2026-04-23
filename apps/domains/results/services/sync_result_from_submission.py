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
    _norm = lambda s: str(s).strip().upper() if s else ""
    items_payload = []
    for q in questions:
        qid = int(q.id)
        ans = answers_map.get(qid, "")
        correct_key = key_map.get(qid, "")
        is_correct = bool(correct_key and _norm(ans) == _norm(correct_key))
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
    result.objective_score = total  # 자동채점 문항 합산 = objective score
    result.submitted_at = timezone.now()
    result.save(update_fields=["total_score", "max_score", "objective_score", "submitted_at", "updated_at"])

    # ✅ ExamAttempt 연동 (ONLINE submissions)
    # P0-2: attempt_index=1 하드코딩 제거 — 기존 attempt를 submission_id로 찾고,
    # 없으면 현재 최대 attempt_index + 1로 생성. 재시험 존재 시 대표 롤백 방지.
    from apps.domains.results.models import ExamAttempt
    from django.db.models import Max

    # 1) 이 submission에 이미 연결된 attempt가 있는지 확인
    attempt = (
        ExamAttempt.objects
        .select_for_update()
        .filter(submission_id=submission.id)
        .first()
    )

    if attempt:
        # 기존 attempt 상태만 갱신 (대표 플래그는 건드리지 않음 — 이미 설정된 상태 유지)
        attempt.status = "done"
        attempt.save(update_fields=["status", "updated_at"])
    else:
        # submission에 연결된 attempt 없음 → 새로 생성
        # (exam, enrollment) lock으로 직렬화
        existing_qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=int(exam.id), enrollment_id=int(enrollment_id))
        )
        last_index = existing_qs.aggregate(Max("attempt_index")).get("attempt_index__max") or 0
        next_index = int(last_index) + 1

        # 기존 대표 해제 후 새 attempt를 대표로 설정
        existing_qs.filter(is_representative=True).update(is_representative=False)

        # attempt_index=1이면 "1차 점수 불변 스냅샷"을 meta에 저장한다.
        # Result.total_score는 ONLINE 재응시 시 sync가 덮어쓰지만,
        # attempt_index=1.meta["initial_snapshot"]은 한 번 저장되면 이후 갱신 경로가 없어
        # 석차 계산(ranking.py)이 "석차=1차 점수" 정책을 유지할 수 있게 해준다.
        new_meta = None
        if next_index == 1:
            new_meta = {
                "initial_snapshot": {
                    "total_score": float(total),
                    "max_score": float(max_total),
                    "submitted_at": timezone.now().isoformat(),
                    "submission_id": int(submission.id),
                }
            }

        from django.db import IntegrityError
        try:
            attempt = ExamAttempt.objects.create(
                exam_id=int(exam.id),
                enrollment_id=int(enrollment_id),
                submission_id=submission.id,
                attempt_index=next_index,
                is_retake=(last_index > 0),
                is_representative=True,
                status="done",
                meta=new_meta,
            )
        except IntegrityError:
            # 동시성: 다른 경로에서 이미 생성됨 — 기존 것 사용
            attempt = (
                ExamAttempt.objects
                .filter(submission_id=submission.id)
                .first()
            )
            if not attempt:
                raise

    result.attempt_id = attempt.id
    result.save(update_fields=["attempt_id", "updated_at"])

    return result
