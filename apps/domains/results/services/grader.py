# apps/domains/results/services/grader.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

# ======================================================
# 🔽 submissions 도메인 (raw input)
# ======================================================
from apps.domains.submissions.models import Submission, SubmissionAnswer

# ======================================================
# 🔽 results 도메인 (apply / attempt)
# ======================================================
from apps.domains.results.services.applier import ResultApplier
from apps.domains.results.services.attempt_service import ExamAttemptService

# ======================================================
# 🔽 exams 도메인 (정답 / 문제 정의)
# ======================================================
from apps.domains.exams.models import ExamQuestion, AnswerKey
# (선택) pass_score를 Exam에서 읽을 수 있으면 쓰고, 없으면 안전하게 스킵
try:
    from apps.domains.exams.models import Exam  # type: ignore
except Exception:  # pragma: no cover
    Exam = None  # type: ignore

# ======================================================
# 🔽 progress pipeline (side-effect)
# ======================================================
from apps.domains.progress.tasks.progress_pipeline_task import (
    run_progress_pipeline_task,
)

# ======================================================
# Constants (STEP 1 고정)
# ======================================================
OMR_CONF_THRESHOLD_V1 = 0.70


# ======================================================
# Utils
# ======================================================
def _norm(s: Optional[str]) -> str:
    """
    문자열 정규화 (STEP 1 exact match 고정):
    - None 방어
    - 공백 제거
    - 대문자 통일
    """
    return (s or "").strip().upper()


def _get_omr_meta(meta: Any) -> Dict[str, Any]:
    """
    submissions.SubmissionAnswer.meta 에서
    omr dict 만 안전하게 추출
    """
    if not isinstance(meta, dict):
        return {}
    omr = meta.get("omr")
    return omr if isinstance(omr, dict) else {}


def _ensure_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _with_invalid_reason(meta: Any, reason: str) -> Dict[str, Any]:
    """
    ✅ STEP 1 핵심:
    low_conf / blank / multi 등 "무효 처리"는 0점 처리 뿐 아니라
    **사유를 append-only로 남겨야 운영/재처리/프론트 표시가 가능**해짐.
    """
    base = _ensure_dict(meta)
    out = dict(base)
    out.setdefault("grading", {})
    if isinstance(out["grading"], dict):
        out["grading"]["invalid_reason"] = reason
    return out


# ======================================================
# Grading helpers
# ======================================================
def _grade_choice_v1(
    *,
    detected: List[str],
    marking: str,
    confidence: Optional[float],
    status: str,
    correct_answer: str,
    max_score: float,
    # ✅ 기존 meta를 받아서 invalid_reason을 심는다
    original_meta: Any,
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    OMR 객관식 채점 v1 (STEP 1 고정)

    ✅ 정책:
    - status != ok -> 무효 (0점)
    - marking blank/multi -> 무효 (0점)
    - confidence < threshold -> 무효 (0점) + LOW_CONFIDENCE 사유 저장  ⭐⭐⭐
    - detected != 1개 -> 무효 (0점)
    """
    st = (status or "").lower()
    mk = (marking or "").lower()

    # 1) status가 ok가 아니면 무효
    if st != "ok":
        return False, 0.0, _with_invalid_reason(original_meta, "OMR_STATUS_NOT_OK")

    # 2) blank/multi는 무효
    if mk in ("blank", "multi"):
        reason = "OMR_BLANK" if mk == "blank" else "OMR_MULTI"
        return False, 0.0, _with_invalid_reason(original_meta, reason)

    # 3) 신뢰도 체크 (STEP 1: low confidence 자동 0점 + 사유 저장)
    conf = float(confidence) if confidence is not None else 0.0
    if conf < OMR_CONF_THRESHOLD_V1:
        return False, 0.0, _with_invalid_reason(original_meta, "LOW_CONFIDENCE")

    # 4) detected 1개 강제
    if not detected or len(detected) != 1:
        return False, 0.0, _with_invalid_reason(original_meta, "OMR_DETECTED_INVALID")

    ans = _norm(detected[0])
    cor = _norm(correct_answer)

    is_correct = ans != "" and cor != "" and ans == cor
    return is_correct, (float(max_score) if is_correct else 0.0), _ensure_dict(original_meta)


def _grade_short_v1(
    *,
    answer_text: str,
    correct_answer: str,
    max_score: float,
    original_meta: Any,
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    주관식 / fallback 채점 (STEP 1: exact match)

    ✅ 정책:
    - empty => 0점
    - exact match only
    """
    ans = _norm(answer_text)
    cor = _norm(correct_answer)

    if ans == "":
        return False, 0.0, _with_invalid_reason(original_meta, "EMPTY_ANSWER")

    is_correct = cor != "" and ans == cor
    return is_correct, (float(max_score) if is_correct else 0.0), _ensure_dict(original_meta)


def _infer_answer_type(q: ExamQuestion) -> str:
    """
    ExamQuestion.answer_type 추론
    """
    v = getattr(q, "answer_type", None)
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return "choice"


def _get_correct_answer_map_v2(exam_id: int) -> Dict[str, Any]:
    """
    ✅ AnswerKey v2 고정

    answers = {
        "123": "B",
        "124": "D"
    }

    key == ExamQuestion.id (string)
    """
    ak = AnswerKey.objects.filter(exam_id=int(exam_id)).first()
    if not ak or not isinstance(ak.answers, dict):
        return {}
    return ak.answers


def _get_pass_score(exam_id: int) -> Optional[float]:
    """
    (선택) Exam.pass_score가 있으면 읽어서 attempt/meta에 기록.
    - ResultApplier가 이미 is_pass를 계산한다면 이건 "진단/표시용" 정보로만 남는다.
    """
    if Exam is None:
        return None
    try:
        exam = Exam.objects.filter(id=int(exam_id)).first()
        if not exam:
            return None
        v = getattr(exam, "pass_score", None)
        return float(v) if v is not None else None
    except Exception:
        return None


# ======================================================
# Main grading pipeline
# ======================================================
@transaction.atomic
def grade_submission_to_results(submission: Submission) -> None:
    """
    Submission → ExamAttempt → Result / ResultItem / ResultFact

    🔥 v2 핵심 계약:
    - SubmissionAnswer.exam_question_id 만 사용
    - number / fallback 완전 제거
    - AnswerKey v2 고정
    - ✅ STEP 1: LOW_CONF 무효 0점 + 사유 저장
    """

    # --------------------------------------------------
    # 0️⃣ Submission 상태 전이
    # --------------------------------------------------
    submission.status = Submission.Status.GRADING
    if hasattr(submission, "error_message"):
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message"])
    else:
        submission.save(update_fields=["status"])

    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("Only exam grading is supported")

    attempt = None

    try:
        # --------------------------------------------------
        # 1️⃣ ExamAttempt 생성
        # --------------------------------------------------
        attempt = ExamAttemptService.create_for_submission(
            exam_id=int(submission.target_id),
            enrollment_id=int(submission.enrollment_id),
            submission_id=int(submission.id),
        )
        attempt.status = "grading"
        attempt.save(update_fields=["status"])

        # --------------------------------------------------
        # 2️⃣ Raw answers
        # --------------------------------------------------
        answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        # --------------------------------------------------
        # 3️⃣ ExamQuestion 로딩 (id 기준)
        # --------------------------------------------------
        questions_by_id = (
            ExamQuestion.objects
            .filter(sheet__exam_id=submission.target_id)
            .in_bulk(field_name="id")
        )

        correct_map = _get_correct_answer_map_v2(int(submission.target_id))

        items: List[dict] = []

        total_score = 0.0
        total_max_score = 0.0

        # --------------------------------------------------
        # 4️⃣ 문항별 채점
        # --------------------------------------------------
        for sa in answers:
            eqid = getattr(sa, "exam_question_id", None)
            if not eqid:
                continue

            try:
                q = questions_by_id.get(int(eqid))
            except (TypeError, ValueError):
                continue

            if not q:
                continue

            max_score = float(getattr(q, "score", 0) or 0.0)
            correct_answer = str(correct_map.get(str(q.id)) or "")

            answer_text = str(getattr(sa, "answer", "") or "").strip()

            # submissions meta
            original_meta = getattr(sa, "meta", None)
            omr = _get_omr_meta(original_meta)

            detected = omr.get("detected") or []
            marking = str(omr.get("marking") or "")
            confidence = omr.get("confidence", None)
            status = str(omr.get("status") or "")
            omr_version = str(omr.get("version") or "")

            # ✅ STEP 1: low_confidence status는 즉시 무효 처리 (0점+사유)
            if (status or "").lower() == "low_confidence":
                is_correct = False
                score = 0.0
                final_answer = ""
                final_meta = _with_invalid_reason(original_meta, "LOW_CONFIDENCE")
            else:
                answer_type = _infer_answer_type(q)

                if answer_type in ("choice", "omr", "multiple_choice"):
                    if omr_version.lower() in ("v1", "v2"):
                        is_correct, score, final_meta = _grade_choice_v1(
                            detected=[str(x) for x in detected],
                            marking=marking,
                            confidence=confidence,
                            status=status,
                            correct_answer=correct_answer,
                            max_score=max_score,
                            original_meta=original_meta,
                        )
                        # 표시용 answer: 감지된 값 1개면 그 값, 아니면 ""
                        final_answer = (
                            "".join([_norm(x) for x in detected]) if detected else ""
                        )
                    else:
                        # OMR meta가 없거나 버전이 없을 때: 텍스트 기반 exact match
                        is_correct, score, final_meta = _grade_short_v1(
                            answer_text=answer_text,
                            correct_answer=correct_answer,
                            max_score=max_score,
                            original_meta=original_meta,
                        )
                        final_answer = answer_text
                else:
                    # subjective: exact match (STEP 1)
                    is_correct, score, final_meta = _grade_short_v1(
                        answer_text=answer_text,
                        correct_answer=correct_answer,
                        max_score=max_score,
                        original_meta=original_meta,
                    )
                    final_answer = answer_text

            # 점수 누적
            total_score += float(score)
            total_max_score += float(max_score)

            items.append({
                "question_id": q.id,
                "answer": final_answer,
                "is_correct": bool(is_correct),
                "score": float(score),
                "max_score": float(max_score),
                "source": submission.source,
                # ✅ 최종 meta에는 invalid_reason이 반영될 수 있음
                "meta": final_meta,
            })

        # --------------------------------------------------
        # 4-1) (선택) attempt/meta에 total/pass 정보 기록
        # - ResultApplier가 실제 ResultSummary.is_pass를 만들더라도,
        #   attempt에는 운영/디버깅용으로 남겨두면 좋음.
        # --------------------------------------------------
        try:
            pass_score = _get_pass_score(int(submission.target_id))
            meta = getattr(attempt, "meta", None)
            if isinstance(meta, dict):
                new_meta = dict(meta)
            else:
                new_meta = {}

            new_meta.setdefault("grading", {})
            if isinstance(new_meta["grading"], dict):
                new_meta["grading"]["total_score"] = float(total_score)
                new_meta["grading"]["total_max_score"] = float(total_max_score)
                if pass_score is not None:
                    new_meta["grading"]["pass_score"] = float(pass_score)
                    new_meta["grading"]["is_pass_inferred"] = bool(total_score >= pass_score)

            if hasattr(attempt, "meta"):
                attempt.meta = new_meta
                attempt.save(update_fields=["meta"])
        except Exception:
            # meta 필드가 없거나 저장 실패해도 grading 자체는 계속 진행
            pass

        # --------------------------------------------------
        # 5️⃣ Result 반영
        # --------------------------------------------------
        ResultApplier.apply(
            target_type=submission.target_type,
            target_id=int(submission.target_id),
            enrollment_id=int(submission.enrollment_id),
            submission_id=int(submission.id),
            attempt_id=int(attempt.id),
            items=items,
        )

        # --------------------------------------------------
        # 6️⃣ 상태 마무리
        # --------------------------------------------------
        attempt.status = "done"
        attempt.save(update_fields=["status"])

        submission.status = Submission.Status.DONE
        submission.save(update_fields=["status"])

        transaction.on_commit(
            lambda: run_progress_pipeline_task.delay(submission.id)
        )

    except Exception as e:
        if attempt:
            attempt.status = "failed"
            attempt.save(update_fields=["status"])

        submission.status = Submission.Status.FAILED
        if hasattr(submission, "error_message"):
            submission.error_message = str(e)[:2000]
            submission.save(update_fields=["status", "error_message"])
        else:
            submission.save(update_fields=["status"])
        raise
