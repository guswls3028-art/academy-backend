# # PATH: apps/domains/results/services/grading_policy.py

# NOTE:
# STEP 2 이후 grader 리팩토링 시 사용할 정책 모듈
# 현재는 미사용

# from __future__ import annotations

# from dataclasses import dataclass
# from typing import Any, Dict, Optional, Tuple


# # =========================================================
# # STEP 1 정책 상수 (고정)
# # =========================================================

# MIN_OMR_CONFIDENCE = 0.70  # 이 미만이면 자동 무효(0점)


# def normalize_text(s: str) -> str:
#     """
#     주관식 exact match 표준 정규화 (STEP 1 고정)
#     - strip + lower
#     - 추후 공백/특수문자 규칙은 여기서만 바꾸면 됨
#     """
#     return (s or "").strip().lower()


# @dataclass(frozen=True)
# class OMRValidity:
#     is_valid: bool
#     invalid_reason: Optional[str] = None


# def evaluate_omr_validity(submission_answer_meta: Dict[str, Any]) -> OMRValidity:
#     """
#     SubmissionAnswer.meta["omr"] 기반으로 OMR 유효성 판단 (STEP 1 고정)
#     - low_confidence면 0점 처리
#     - ambiguous/multi/blank은 너의 채점 정책에 따라 0점/부분점 등 확장 가능하지만
#       STEP 1에서는 최소한 low_conf는 무조건 무효로 고정.
#     """
#     omr = (submission_answer_meta or {}).get("omr") or {}
#     conf = omr.get("confidence")
#     status = str(omr.get("status") or "").lower()

#     try:
#         conf_f = float(conf) if conf is not None else None
#     except Exception:
#         conf_f = None

#     # ✅ 명시적으로 low_confidence면 무효
#     if status == "low_confidence":
#         return OMRValidity(is_valid=False, invalid_reason="LOW_CONFIDENCE")

#     # ✅ confidence 값이 있고 임계치 미만이면 무효
#     if conf_f is not None and conf_f < MIN_OMR_CONFIDENCE:
#         return OMRValidity(is_valid=False, invalid_reason="LOW_CONFIDENCE")

#     return OMRValidity(is_valid=True, invalid_reason=None)


# def grade_subjective_exact(answer: str, correct: str, full_score: float) -> float:
#     """
#     주관식 exact match 채점 (STEP 1 고정)
#     """
#     return float(full_score) if normalize_text(answer) == normalize_text(correct) else 0.0


# def grade_choice_exact(answer: str, correct: str, full_score: float) -> float:
#     """
#     객관식 exact match 기본 (A/B/C/D)
#     - 다중마킹 처리/부분점은 STEP 2 이후 확장 포인트
#     """
#     return float(full_score) if normalize_text(answer) == normalize_text(correct) else 0.0


# def evaluate_pass_fail(total_score: float, pass_score: float) -> bool:
#     """
#     시험 단위 pass/fail (STEP 1 고정)
#     """
#     try:
#         return float(total_score) >= float(pass_score)
#     except Exception:
#         return False
