"""
식별 매칭 → ExamEnrollment 락 → duplicate conflict 의 단계 결과를 한 곳에 봉인.

이전: ai_omr_result_mapper 본문에서
        identifier_id 결정 → lock_exam_enrollment_candidate → find_conflicting
        → identifier_status 라벨 결정 → reasons append 가 줄 단위로 흩어져 있었고,
        실패 경로마다 enrollment_id / identifier_status / manual_required 를
        손으로 되감았다 (테스트 시 의도 파악 어려움).

이 모듈은 그 묶음을 단일 함수로 봉인하고, 결과 dataclass 만으로 mapper 가
meta 와 transit 을 결정할 수 있게 한다.

책임:
- IdentifierMatcher 한 번 호출.
- ExamEnrollment 락 (lock_exam_enrollment_candidate). 실패 시 enrollment_id 를
  None 으로 되감고 identifier_status='no_match' + IDENTIFIER_NO_EXAM_ENROLLMENT
  reason 부여.
- 같은 시험·학생의 다른 active sub 발견 시 duplicate_conflict 표면화 + reason.

비책임 (의도적 제외):
- meta 직접 변경: mapper 가 결과 dataclass 를 보고 meta 를 빌드.
- 상태 전이: mapper 가 transit() 호출.
- answer 처리: answer_persister.persist_answers() 가 담당.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from apps.domains.submissions.models import Submission
from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
    IdentifierMatcher,
    IdentifierMatchResult,
)
from apps.domains.submissions.services.omr_submission_guards import (
    find_conflicting_exam_submission,
)
from apps.support.omr.candidate_matching import lock_exam_enrollment_candidate


@dataclass
class EnrollmentFinalizeResult:
    """식별·매칭·락·duplicate 검사의 한 줄 출력."""

    enrollment_id: Optional[int]
    identifier_status: str
    identifier_match_kind: str  # "exact" | "fuzzy" | "none"
    identifier_ok: bool
    duplicate_conflict: Optional[Submission]
    review_reasons: list[str] = field(default_factory=list)
    manual_required: bool = False
    # 진단용 (UI cluster / review 표시)
    detected_code: str = ""
    raw_identifier_status: str = ""


def finalize_enrollment(
    *,
    submission: Submission,
    identifier_payload: Any,
) -> EnrollmentFinalizeResult:
    """
    OMR submission 의 식별 + enrollment 락 + duplicate 검사 일괄 처리.

    Args:
        submission: select_for_update 잡힌 Submission. target_type=EXAM 가정.
        identifier_payload: worker result.identifier 그대로 (None / dict 모두 OK).
    """
    if submission.target_id and isinstance(identifier_payload, dict):
        match_result = IdentifierMatcher(
            tenant=submission.tenant,
            exam_id=int(submission.target_id),
        ).match(identifier_payload)
    else:
        match_result = IdentifierMatchResult(
            None, "missing", False, ["IDENTIFIER_MISSING"]
        )

    enrollment_id = match_result.enrollment_id
    identifier_status = match_result.identifier_status
    identifier_match_kind = _match_kind_label(match_result)
    manual_required = match_result.needs_review

    reasons: list[str] = [
        r
        for r in match_result.review_reasons
        if r != "IDENTIFIER_AMBIGUOUS_DIGIT_RESOLVED"
    ]

    detected_code = ""
    raw_identifier_status = ""
    if isinstance(identifier_payload, dict):
        detected_code = str(
            identifier_payload.get("identifier")
            or identifier_payload.get("raw_identifier")
            or ""
        ).strip()
        raw_identifier_status = str(
            identifier_payload.get("status") or ""
        ).lower()

    identifier_ok = enrollment_id is not None
    duplicate_conflict: Optional[Submission] = None

    if identifier_ok and submission.target_id:
        locked = lock_exam_enrollment_candidate(
            tenant=submission.tenant,
            exam_id=int(submission.target_id),
            enrollment_id=int(enrollment_id),
        )
        if not locked:
            # SessionEnrollment fallback 도 실패 → 시험 대상자가 아니다.
            enrollment_id = None
            identifier_status = "no_match"
            identifier_ok = False
            reasons.append("IDENTIFIER_NO_EXAM_ENROLLMENT")
        else:
            duplicate_conflict = find_conflicting_exam_submission(
                tenant=submission.tenant,
                exam_id=int(submission.target_id),
                enrollment_id=int(enrollment_id),
                exclude_submission_id=int(submission.id),
            )
            if duplicate_conflict is not None:
                manual_required = True
                identifier_status = "matched_duplicate"
                reasons.append("DUPLICATE_ENROLLMENT")

    return EnrollmentFinalizeResult(
        enrollment_id=enrollment_id,
        identifier_status=identifier_status,
        identifier_match_kind=identifier_match_kind,
        identifier_ok=identifier_ok,
        duplicate_conflict=duplicate_conflict,
        review_reasons=reasons,
        manual_required=manual_required,
        detected_code=detected_code,
        raw_identifier_status=raw_identifier_status,
    )


def _match_kind_label(match_result: IdentifierMatchResult) -> str:
    if match_result.kind == "fuzzy":
        return "fuzzy"
    if match_result.kind in ("exact", "exact_with_competitor"):
        return "exact"
    return "none"
