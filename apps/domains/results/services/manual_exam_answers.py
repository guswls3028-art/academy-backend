"""Atomic teacher entry of objective answers when no OMR result owns a student."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.domains.results.guards.exam_enrollment_guard import validate_exam_enrollment_assigned
from apps.domains.results.guards.score_edit_lease_guard import (
    require_score_edit_scope_available_for_exam,
)
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.domains.results.services.answer_matching import (
    answer_matches,
    correct_answer_sets,
    student_answer_set,
)
from apps.domains.results.services.exam_result_excel_import import (
    ExamResultWorkbookError,
    _exam_candidates,
    _locked_result_and_attempt,
    _question_specs,
)
from apps.support.exams.numeric_short_answer import (
    math_numeric_short_answer_question_ids,
    normalize_numeric_short_answer,
    numeric_short_answer_matches,
)
from apps.support.omr.score_adjustment import get_score_adjustment_from_answers
from apps.support.omr.score_shape import get_exam_score_shape
from apps.support.results.admin_exam_dependencies import dispatch_progress_pipeline
from apps.support.results.exam_result_excel_import_dependencies import (
    get_answer_key_answers,
    get_locked_exam_for_tenant,
    get_locked_enrollment_for_tenant,
    has_active_exam_submission,
)


@dataclass(frozen=True)
class ScoredAnswer:
    question_id: int
    number: int
    answer: str
    is_correct: bool
    score: float
    max_score: float


def _version(result: Result | None) -> str | None:
    return result.updated_at.isoformat() if result is not None else None


def _answer_fingerprint(result: Result) -> str:
    items = list(ResultItem.objects.filter(result=result).order_by("question_id").values_list(
        "question_id", "answer", "is_correct", "score", "max_score", "source",
    ))
    payload = [result.total_score, result.objective_score, result.max_score, items]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _current_manual_result(*, exam: Any, tenant: Any, enrollment_id: int) -> Result | None:
    validate_exam_enrollment_assigned(exam, enrollment_id)
    if not any(
        candidate.enrollment_id == enrollment_id
        for candidate in _exam_candidates(exam=exam, tenant=tenant)
    ):
        raise ValidationError({"enrollment_id": "이 시험의 활성 응시 대상이 아닙니다."})

    if has_active_exam_submission(exam_id=exam.id, enrollment_id=enrollment_id, tenant=tenant):
        raise ValidationError({"detail": "등록된 OMR 답안은 OMR 검토에서 보정해 주세요."})

    result = Result.objects.filter(
        target_type="exam", target_id=exam.id, enrollment_id=enrollment_id,
    ).select_related("attempt").first()
    if result is not None:
        attempt = result.attempt
        meta = attempt.meta if attempt and isinstance(attempt.meta, dict) else {}
        if (
            attempt is None
            or int(attempt.exam_id) != int(exam.id)
            or int(attempt.enrollment_id) != enrollment_id
            or int(attempt.submission_id or 0) != 0
            or (
                str(meta.get("source") or "") != "manual_entry"
                and str(meta.get("status") or "") != "NOT_SUBMITTED"
            )
        ):
            raise ValidationError({"detail": "기존 성적의 출처를 확인한 뒤 해당 보정 화면에서 수정해 주세요."})
        if attempt.status == "grading":
            raise ValidationError({"detail": "현재 채점 중입니다. 잠시 후 다시 시도해 주세요."})
    elif ExamAttempt.objects.filter(
        exam=exam, enrollment_id=enrollment_id, is_representative=True,
    ).exists():
        raise ValidationError({"detail": "기존 응시 기록을 확인한 뒤 수정해 주세요."})
    return result


def get_manual_answers(*, exam: Any, tenant: Any, enrollment_id: int) -> dict[str, Any]:
    result = _current_manual_result(exam=exam, tenant=tenant, enrollment_id=enrollment_id)
    return {
        "exam_id": int(exam.id),
        "enrollment_id": enrollment_id,
        "expected_version": _version(result),
        "answers": {
            str(question_id): answer
            for question_id, answer in ResultItem.objects.filter(result=result).values_list("question_id", "answer")
        } if result is not None else {},
    }


def preview_manual_answers(
    *, exam: Any, tenant: Any, enrollment_id: int, payload: Any,
) -> tuple[dict[str, Any], list[ScoredAnswer]]:
    if exam.grading_mode != "choice":
        raise ValidationError({"detail": "혼합형·서술형 시험은 직접 채점표에서 입력해 주세요."})
    if not isinstance(payload, dict):
        raise ValidationError({"detail": "답안 형식을 확인해 주세요."})
    note = str(payload.get("note") or "").strip()
    if len(note) < 2 or len(note) > 500:
        raise ValidationError({"note": "입력·수정 사유를 2~500자로 적어 주세요."})

    result = _current_manual_result(exam=exam, tenant=tenant, enrollment_id=enrollment_id)
    expected_version = payload.get("expected_version")
    if expected_version not in (None, "") and not isinstance(expected_version, str):
        raise ValidationError({"expected_version": "결과 버전 형식을 확인해 주세요."})
    if (expected_version or None) != _version(result):
        raise ValidationError({"expected_version": "다른 화면에서 성적이 변경됐습니다. 다시 불러와 주세요."})

    try:
        questions = _question_specs(exam=exam, tenant=tenant)
    except ExamResultWorkbookError as exc:
        raise ValidationError({"detail": str(exc)}) from exc
    score_shape = get_exam_score_shape(exam)
    key_answers = get_answer_key_answers(template_exam_id=score_shape.template_exam_id)
    if not isinstance(key_answers, dict):
        raise ValidationError({"detail": "정답을 먼저 등록해 주세요."})
    numeric_ids = math_numeric_short_answer_question_ids(
        exam=exam,
        question_ids=(question.question_id for question in questions),
        question_kind=score_shape.question_kind,
        answers=key_answers,
    )
    objective_questions = [
        question for question in questions
        if question.kind == "choice" or question.question_id in numeric_ids
    ]
    if not objective_questions:
        raise ValidationError({"detail": "이 시험에는 직접 입력할 객관식·숫자 답안이 없습니다."})
    if len(objective_questions) != len(questions):
        raise ValidationError({"detail": "서술형 문항은 직접 채점표에서 먼저 처리해 주세요."})

    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, dict):
        raise ValidationError({"answers": "모든 채점 문항의 답안을 입력해 주세요."})
    expected_keys = {str(question.question_id) for question in objective_questions}
    if set(raw_answers) != expected_keys:
        raise ValidationError({"answers": "현재 채점 문항 전체를 다시 불러와 입력해 주세요."})
    if result is not None and ResultItem.objects.filter(result=result).exclude(
        question_id__in=[question.question_id for question in objective_questions]
    ).exists():
        raise ValidationError({"detail": "이전 문항의 성적이 남아 있습니다. 기존 성적을 먼저 확인해 주세요."})

    scored: list[ScoredAnswer] = []
    for question in objective_questions:
        qid = question.question_id
        raw = raw_answers[str(qid)]
        if not isinstance(raw, str):
            raise ValidationError({"answers": f"{question.number}번 답안 형식을 확인해 주세요."})
        answer = raw.strip()
        key = key_answers.get(str(qid))
        if key is None or key == "":
            raise ValidationError({"answers": f"{question.number}번 정답을 먼저 등록해 주세요."})
        if qid in numeric_ids:
            if answer and normalize_numeric_short_answer(answer) is None:
                raise ValidationError({"answers": f"{question.number}번은 0~999 정수로 입력해 주세요."})
            answer = normalize_numeric_short_answer(answer) if answer else ""
            correct = numeric_short_answer_matches(answer, key)
        else:
            marked = student_answer_set(answer)
            if any(token not in {"1", "2", "3", "4", "5"} for token in marked):
                raise ValidationError({"answers": f"{question.number}번은 1~5번 선택지만 입력해 주세요."})
            if not correct_answer_sets(key):
                raise ValidationError({"answers": f"{question.number}번 정답을 확인해 주세요."})
            answer = ",".join(sorted(marked))
            correct = answer_matches(answer, key)
        scored.append(ScoredAnswer(
            question_id=qid,
            number=question.number,
            answer=answer,
            is_correct=correct,
            score=float(question.max_score) if correct else 0.0,
            max_score=float(question.max_score),
        ))

    adjustment = get_score_adjustment_from_answers(key_answers)
    bonus = float(adjustment.objective)
    if numeric_ids:
        bonus += float(adjustment.subjective)
    objective_score = round(sum(item.score for item in scored) + bonus, 2)
    total_score = objective_score
    max_score = float(score_shape.total_max_score or exam.max_score or 0)
    if max_score <= 0 or total_score > max_score + 0.0001:
        raise ValidationError({"detail": "문항 배점·만점 구성이 점수와 맞지 않습니다. 시험 설정을 확인해 주세요."})
    preview_token = hashlib.sha256(json.dumps([
        exam.id, enrollment_id, _version(result),
        _answer_fingerprint(result) if result is not None else None,
        _version(exam), note,
        key_answers, [item.__dict__ for item in scored], total_score, max_score,
    ], sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return ({
        "exam_id": int(exam.id),
        "enrollment_id": enrollment_id,
        "expected_version": _version(result),
        "objective_score": objective_score,
        "total_score": total_score,
        "max_score": max_score,
        "subjective_pending": False,
        "questions": [item.__dict__ for item in scored],
        "preview_token": preview_token,
    }, scored)


@transaction.atomic
def apply_manual_answers(
    *, exam: Any, tenant: Any, enrollment_id: int, payload: Any, user_id: int,
) -> dict[str, Any]:
    exam = get_locked_exam_for_tenant(exam_id=exam.id, tenant=tenant)
    if exam is None:
        raise ValidationError({"detail": "시험을 찾을 수 없습니다."})
    require_score_edit_scope_available_for_exam(exam=exam, tenant=tenant)
    enrollment = get_locked_enrollment_for_tenant(enrollment_id=enrollment_id, tenant=tenant)
    if enrollment is None:
        raise ValidationError({"enrollment_id": "수강 정보를 찾을 수 없습니다."})
    preview, scored = preview_manual_answers(
        exam=exam, tenant=tenant, enrollment_id=enrollment_id, payload=payload,
    )
    if not isinstance(payload, dict) or payload.get("preview_token") != preview["preview_token"]:
        raise ValidationError({"preview_token": "미리보기 이후 시험 또는 답안이 변경됐습니다. 다시 확인해 주세요."})
    prior_version = preview["expected_version"]
    result, attempt = _locked_result_and_attempt(
        exam=exam, enrollment=enrollment,
        initial_total=preview["total_score"], initial_max=preview["max_score"],
        is_not_submitted=False, now=timezone.now(),
    )
    if prior_version is not None and _version(result) != prior_version:
        raise ValidationError({"expected_version": "다른 화면에서 성적이 변경됐습니다. 다시 불러와 주세요."})
    now = timezone.now()
    meta = dict(attempt.meta or {}) if isinstance(attempt.meta, dict) else {}
    was_absent = meta.get("status") == "NOT_SUBMITTED"
    if was_absent:
        meta["previous_absence"] = {
            "source": meta.get("source"), "corrected_at": now.isoformat(), "user_id": user_id,
        }
    meta["source"] = "manual_entry"
    meta.pop("status", None)
    if was_absent:
        meta["initial_snapshot"] = {
            "total_score": preview["total_score"], "max_score": preview["max_score"],
            "submitted_at": now.isoformat(), "source": "manual_entry",
        }
    elif prior_version is None and isinstance(meta.get("initial_snapshot"), dict):
        meta["initial_snapshot"]["source"] = "manual_entry"
    meta["total_score"] = preview["total_score"]
    meta["max_score"] = preview["max_score"]
    meta["last_manual_answer_entry"] = {"user_id": user_id, "at": now.isoformat()}
    attempt.meta = meta
    attempt.status = "done"
    attempt.save(update_fields=["meta", "status", "updated_at"])

    previous = {
        item.question_id: item for item in ResultItem.objects.select_for_update().filter(result=result)
    }
    for answer in scored:
        old = previous.get(answer.question_id)
        ResultFact.objects.create(
            target_type="exam", target_id=exam.id, enrollment=enrollment,
            submission_id=0, attempt=attempt, question_id=answer.question_id,
            answer=answer.answer, is_correct=answer.is_correct,
            score=answer.score, max_score=answer.max_score, source="manual",
            meta={"note": payload["note"].strip(), "user_id": user_id,
                  "previous_answer": old.answer if old else None, "edited_at": now.isoformat()},
        )
        ResultItem.objects.update_or_create(
            result=result, question_id=answer.question_id,
            defaults={"answer": answer.answer, "is_correct": answer.is_correct,
                      "score": answer.score, "max_score": answer.max_score, "source": "manual"},
        )
    result.attempt = attempt
    result.objective_score = preview["objective_score"]
    result.total_score = preview["total_score"]
    result.max_score = preview["max_score"]
    result.submitted_at = now
    result.save(update_fields=["attempt", "objective_score", "total_score", "max_score", "submitted_at", "updated_at"])
    meta["manual_answer_result_version"] = _version(result)
    meta["manual_answer_fingerprint"] = _answer_fingerprint(result)
    attempt.meta = meta
    attempt.save(update_fields=["meta", "updated_at"])
    dispatch_progress_pipeline(exam_id=int(exam.id))
    return {**preview, "applied": True, "expected_version": _version(result)}


def regrade_manual_exam_answers(*, exam: Any, tenant: Any) -> dict[str, Any]:
    """Re-score untouched offline answer entries against the current answer key."""
    results = list(Result.objects.filter(
        target_type="exam", target_id=exam.id, enrollment__tenant=tenant,
        attempt__submission_id=0, attempt__meta__source="manual_entry",
    ).order_by("id").values_list("id", "enrollment_id"))
    graded = 0
    needs_review: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for result_id, enrollment_id in results:
        result = Result.objects.select_related("attempt").get(id=result_id)
        meta = result.attempt.meta if isinstance(result.attempt.meta, dict) else {}
        if meta.get("manual_answer_fingerprint") != _answer_fingerprint(result):
            needs_review.append({"enrollment_id": enrollment_id, "detail": "수기 점수 보정 기록을 확인해 주세요."})
            continue
        answers = {
            str(question_id): answer
            for question_id, answer in ResultItem.objects.filter(result=result).values_list("question_id", "answer")
        }
        try:
            payload = {"answers": answers, "expected_version": _version(result),
                       "note": "정답표 변경에 따른 자동 재채점"}
            preview, _ = preview_manual_answers(
                exam=exam, tenant=tenant, enrollment_id=enrollment_id, payload=payload,
            )
            payload["preview_token"] = preview["preview_token"]
            apply_manual_answers(
                exam=exam, tenant=tenant, enrollment_id=enrollment_id,
                payload=payload,
                user_id=0,
            )
        except Exception as exc:
            failed.append({"enrollment_id": enrollment_id, "detail": str(exc) or exc.__class__.__name__})
        else:
            graded += 1
    return {"total": len(results), "graded": graded, "needs_review": needs_review, "failed": failed}
