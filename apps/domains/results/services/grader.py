# apps/domains/results/services/grader.py
"""
⚠️ NOTE: 이 모듈의 _grade_choice_v1 / _grade_short_v1은 현재 production 채점 경로에서
   사용되지 않는다. 실제 OMR/객관식 채점은 ExamGradingService._compute_score
   (results/services/exam_grading_service.py)가 수행하며, 거기서는
   SubmissionAnswer.answer 문자열만 AnswerKey와 비교한다 (status/confidence 무관 best-effort).

   본 함수들은 향후 grader 정책 통합 시 참조 SSOT로 두고, test_omr_pipeline.py A9이
   소스 존재만 검증하므로 보존. 변경 시 두 곳을 함께 갱신할 것.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ======================================================
# 🔽 submissions 도메인 (raw input)
# ======================================================

# ======================================================
# 🔽 results 도메인 (apply / attempt)
# ======================================================

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
    OMR 객관식 채점 v2 (best-effort + manual_review)

    ✅ 정책:
    - status == error        -> 무효 (0점)         — 인식 자체 실패
    - marking blank/multi    -> 무효 (0점)         — 학생이 답을 안 함/이중 마킹
    - detected != 1개        -> 무효 (0점)         — 출력 contract 위반
    - status == ambiguous + single detected -> best-effort + AMBIGUOUS_SINGLE 사유 ⭐
    - confidence < threshold -> best-effort 채점 + LOW_CONFIDENCE 사유  ⭐⭐⭐
        · 점수는 정답 일치 여부로 산출 (자동 0점 폐기 — v1 정책 변경)
        · 사유는 그대로 남아 clinic/manual_review 트리거 유지
        · 운영자는 OMR 검토 UI에서 confirm 또는 수정 → 재채점

    근거: low_conf로 자동 0점 처리하면 흐린 마킹 학생 다수가 사실상 자동채점 무력화.
    AI 결과를 best-effort로 사용하고 review UI를 통해 확정하는 흐름이 운영 정확도 ↑.
    status=ambiguous라도 detected==[1개]면 본질은 low_conf와 동일 (gap만 작음).
    """
    st = (status or "").lower()
    mk = (marking or "").lower()

    # 1) status가 error/blank이면 무효 (인식 실패)
    if st == "error":
        return False, 0.0, _with_invalid_reason(original_meta, "OMR_STATUS_ERROR")
    if st == "blank":
        return False, 0.0, _with_invalid_reason(original_meta, "OMR_BLANK")

    # 2) marking이 blank/multi면 무효 (학생이 답을 안 함/다중 마킹)
    if mk in ("blank", "multi"):
        reason = "OMR_BLANK" if mk == "blank" else "OMR_MULTI"
        return False, 0.0, _with_invalid_reason(original_meta, reason)

    # 3) detected 1개 강제
    if not detected or len(detected) != 1:
        return False, 0.0, _with_invalid_reason(original_meta, "OMR_DETECTED_INVALID")

    # 4) best-effort 채점
    ans = _norm(detected[0])
    cor = _norm(correct_answer)
    is_correct = ans != "" and cor != "" and ans == cor
    score = float(max_score) if is_correct else 0.0

    # 5) ambiguous(top-2 gap이 작음)도 single이면 best-effort
    if st == "ambiguous":
        return is_correct, score, _with_invalid_reason(original_meta, "AMBIGUOUS_SINGLE")

    # 6) low_conf는 review 사유만 남기고 점수는 best-effort 유지
    conf = float(confidence) if confidence is not None else 0.0
    if conf < OMR_CONF_THRESHOLD_V1:
        return is_correct, score, _with_invalid_reason(original_meta, "LOW_CONFIDENCE")

    return is_correct, score, _ensure_dict(original_meta)


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
