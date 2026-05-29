"""
OMR 식별번호 → enrollment 매칭 단일 진입점.

이전에는 ai_omr_result_mapper 본문에 80여 줄, candidate_matching 에 또 흩어져
있었다. 그 사이에 다음 silent 결함이 있었다:

    워커가 ident.status='ok' 로 보고하면 backend 가 "정답"으로 신뢰하고
    1 자리 오인식을 검증하지 않는다. 같은 시험에 phone tail 이 1 자리만
    다른 학생이 있으면 다른 학생으로 자동 매칭될 수 있다.
    (실 prod 감지 어려움 — 학원장이 신고해야 알 수 있음.)

이 모듈이 새 단일 invariant 를 강제한다:

    어떤 매칭이든 같은 시험 내에서 1 자리 변형이 다른 학생을 가리키면
    needs_review 가 켜진다. 워커 status 가 'ok' 든 'ambiguous' 든 동일.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from apps.support.omr.candidate_matching import (
    _candidate_enrollments,
    clean_tail8,
    resolve_enrollment_by_identifier,
    tail8_variants,
)


@dataclass(frozen=True)
class IdentifierMatchResult:
    """식별 단계의 표준 출력. ai_omr_result_mapper 가 meta 에 그대로 반영한다."""

    enrollment_id: Optional[int]
    kind: str  # "exact" | "exact_with_competitor" | "fuzzy" | "no_match" | "incomplete" | "missing"
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)

    @property
    def identifier_status(self) -> str:
        """기존 meta['identifier_status'] 와 호환되는 라벨."""
        if self.enrollment_id is None:
            if self.kind == "missing":
                return "missing"
            if self.kind == "incomplete":
                return "incomplete"
            return "no_match"
        if self.kind == "exact_with_competitor":
            return "matched_ambiguous"
        if self.kind == "fuzzy":
            return "matched_fuzzy"
        if "IDENTIFIER_AMBIGUOUS_DIGIT_RESOLVED" in self.review_reasons:
            return "matched_ambiguous_resolved"
        return "matched"


class IdentifierMatcher:
    """
    시험 한 개에 대한 식별번호 → enrollment 매칭기.

    인스턴스 단위로 _candidate_enrollments 결과를 1회만 fetch 해 캐시한다.
    한 배치에서 같은 시험의 OMR 여러 장이 들어와도 candidate 조회를 1번만
    한다 (이전: sub 마다 매번 fetch).
    """

    def __init__(self, *, tenant, exam_id: int):
        self.tenant = tenant
        self.exam_id = int(exam_id)
        self._tails_cache: Optional[dict[int, set[str]]] = None

    # ---------------------------------------------------------------- public
    def match(self, identifier_payload: Any) -> IdentifierMatchResult:
        if not isinstance(identifier_payload, dict):
            return IdentifierMatchResult(None, "missing", False, ["IDENTIFIER_MISSING"])

        ident_status = str(identifier_payload.get("status") or "").lower()
        detected = str(
            identifier_payload.get("identifier")
            or identifier_payload.get("raw_identifier")
            or ""
        ).strip()

        if not detected:
            return IdentifierMatchResult(None, "missing", False, ["IDENTIFIER_MISSING"])

        ident_complete = (
            len(detected) == 8
            and "?" not in detected
            and ident_status in ("ok", "ambiguous")
        )
        if not ident_complete:
            return IdentifierMatchResult(
                None, "incomplete", False, ["IDENTIFIER_INCOMPLETE"]
            )

        enr_id, kind = resolve_enrollment_by_identifier(
            exam_id=self.exam_id,
            identifier=detected,
            tenant=self.tenant,
        )
        if enr_id is None:
            return IdentifierMatchResult(
                None, "no_match", False, ["IDENTIFIER_NO_ENROLLMENT_MATCH"]
            )

        if kind == "fuzzy":
            reasons = ["IDENTIFIER_FUZZY_MATCH"]
            if ident_status == "ambiguous":
                reasons.append("IDENTIFIER_AMBIGUOUS_DIGIT")
            return IdentifierMatchResult(enr_id, "fuzzy", True, reasons)

        # exact match — 워커가 흔들리는 신호(status='ambiguous' 또는 한 자리라도
        # status='ambiguous')를 보인 경우에 한해 1 자리 변형 검증을 수행한다.
        # 워커가 모든 자리 ok 로 보고한 경우 워커를 신뢰한다 (false positive 0,
        # silent 1-digit error 방어는 ambiguous 신호가 있을 때만).
        if self._should_check_competitor(ident_status, identifier_payload):
            has_competitor = self._has_one_digit_competitor(
                detected_code=detected,
                accepted_enrollment_id=int(enr_id),
                identifier_payload=identifier_payload,
            )
            if has_competitor:
                return IdentifierMatchResult(
                    enr_id, "exact_with_competitor", True, ["IDENTIFIER_AMBIGUOUS_DIGIT"]
                )
            if ident_status == "ambiguous":
                return IdentifierMatchResult(
                    enr_id, "exact", False, ["IDENTIFIER_AMBIGUOUS_DIGIT_RESOLVED"]
                )
        return IdentifierMatchResult(enr_id, "exact", False, [])

    # --------------------------------------------------------------- private
    @staticmethod
    def _should_check_competitor(
        ident_status: str, identifier_payload: dict[str, Any]
    ) -> bool:
        """
        워커가 흔들렸다는 신호가 있을 때만 competitor 검증.

        - ident_status='ambiguous'  → 검증
        - ident_status='ok' 이지만 digits 안에 status='ambiguous' 자리 1 개라도
          있으면 → 검증
        - 그 외 (모든 자리 ok) → skip (워커 신뢰)
        """
        if ident_status == "ambiguous":
            return True
        digits = identifier_payload.get("digits")
        if not isinstance(digits, list):
            return False
        for d in digits:
            if isinstance(d, dict) and str(d.get("status") or "").lower() == "ambiguous":
                return True
        return False

    def _has_one_digit_competitor(
        self,
        *,
        detected_code: str,
        accepted_enrollment_id: int,
        identifier_payload: dict[str, Any],
    ) -> bool:
        """
        accepted_code 와 1 자리만 다른 변형이 같은 시험 내 다른 학생을 가리키는가.

        ambiguous digit 정보가 있으면 그 자리 후보만 우선 검사 (성능 + 신호 강함).
        그 외에는 8 자리 × 0~9 = 72 변형 모두 시도해 silent 1-digit error 를 방어.
        """
        base = clean_tail8(detected_code)
        if len(base) != 8:
            return False

        ambiguous_alts = self._extract_ambiguous_alternatives(base, identifier_payload)
        candidates: set[str] = (
            ambiguous_alts if ambiguous_alts else self._all_one_digit_alternatives(base)
        )
        if not candidates:
            return False

        tails_by_enr = self._tails_index()
        for enr_id, tails in tails_by_enr.items():
            if enr_id == accepted_enrollment_id:
                continue
            if candidates & tails:
                return True
        return False

    @staticmethod
    def _extract_ambiguous_alternatives(
        base: str, identifier_payload: dict[str, Any]
    ) -> set[str]:
        digits = identifier_payload.get("digits")
        if not isinstance(digits, list):
            return set()
        alternatives: set[str] = set()
        for digit in digits:
            if not isinstance(digit, dict):
                continue
            if str(digit.get("status") or "").lower() != "ambiguous":
                continue
            try:
                raw_idx = int(digit.get("digit_index"))
            except Exception:
                continue
            idx = raw_idx if 0 <= raw_idx < 8 else raw_idx - 1
            if idx < 0 or idx >= 8:
                continue
            marks = digit.get("marks")
            if not isinstance(marks, list):
                continue
            for mark in marks[1:4]:
                if not isinstance(mark, dict):
                    continue
                raw_number = mark.get("number")
                if raw_number is None:
                    continue
                alt = str(raw_number)
                if len(alt) != 1 or not alt.isdigit() or alt == base[idx]:
                    continue
                alternatives.add(f"{base[:idx]}{alt}{base[idx + 1:]}")
        return alternatives

    @staticmethod
    def _all_one_digit_alternatives(base: str) -> set[str]:
        out: set[str] = set()
        for idx in range(8):
            for alt in "0123456789":
                if alt == base[idx]:
                    continue
                out.add(f"{base[:idx]}{alt}{base[idx + 1:]}")
        return out

    def _tails_index(self) -> dict[int, set[str]]:
        if self._tails_cache is not None:
            return self._tails_cache
        cache: dict[int, set[str]] = {}
        for enr in _candidate_enrollments(exam_id=self.exam_id, tenant=self.tenant):
            student = getattr(enr, "student", None)
            if not student:
                continue
            tails: set[str] = set()
            for source in ("phone", "parent_phone", "omr_code"):
                value = getattr(student, source, "") or ""
                for tail in tail8_variants(value):
                    if len(tail) == 8:
                        tails.add(tail)
            if tails:
                cache[int(enr.id)] = tails
        self._tails_cache = cache
        return cache
