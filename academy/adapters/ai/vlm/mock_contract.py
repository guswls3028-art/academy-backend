"""Stage 5.4 (2026-05-06) — VLM (Vision Language Model) mock contract.

scope: schema + dataclass 만 정의. 실 VLM API 호출 X. 비용 발생 X.

원칙 (사용자 directive):
- VLM 결과는 확정자 X — ProblemSegmentationProposal 후보 (Stage 3 helper 와 동일).
- VLM 결과는 manual_overlap validator 통과 필수 (Stage 3 Phase 3.2 helper).
- VLM trigger 조건: Tier 0/Tier 1 실패 또는 unknown layout 페이지.
- 비용 cap: 월 $50/tenant, doc 당 cap, page limit, timeout, retry 1회.

본 모듈은:
- request/response dataclass 정의
- JSON schema validator (mock 응답 검증)
- needs_vlm_fallback 휴리스틱 (Tier 0 결과 분석 → VLM 호출 필요 여부 판단)
- mock client (실 호출 대신 미리 정의된 응답 반환 — 테스트/dispatcher 검증용)

dispatcher 통합은 Stage 5.5+ 영역.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class VlmPageType(str, Enum):
    """VLM 이 분류하는 페이지 type — Stage 3 ProblemSegmentationProposal 의
    page_role 과 매핑."""
    PROBLEM = "problem"
    ANSWER = "answer"
    COVER = "cover"
    INDEX = "index"
    EXPLANATION = "explanation"
    UNKNOWN = "unknown"


@dataclass
class VlmBbox:
    """VLM bbox — proposal 모델 형식과 동일 (x, y, w, h, norm)."""
    x: float
    y: float
    w: float
    h: float
    norm: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "norm": self.norm}


@dataclass
class VlmProblemDetection:
    """VLM 이 검출한 단일 problem."""
    number: int
    bbox: VlmBbox
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "bbox": self.bbox.to_dict(),
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class VlmPageRequest:
    """VLM 호출 요청 — 단일 페이지 단위.

    본 contract 는 호출 대신 mock client 가 처리. 실 API 호출 금지.
    """
    document_id: int
    page_number: int
    page_image_key: str  # R2 image key (Tier 0/1 가 미리 cache 한 페이지 PNG)
    paper_type_hint: str  # tier0_native_pdf 의 paper_type prototype
    tier0_anchor_count: int  # Tier 0 검출한 anchor 수 (VLM 의 정밀화 baseline)
    tier1_required: bool  # Tier 0 실패 여부
    operating_problem_count: Optional[int] = None  # 운영 doc problem_count (있으면 expected hint)


@dataclass
class VlmPageResponse:
    """VLM 호출 응답 — proposal 후보 list."""
    page_type: VlmPageType
    problems: list[VlmProblemDetection]
    needs_review: bool = False
    confidence: float = 0.0
    raw_response: dict[str, Any] = field(default_factory=dict)
    cost_usd_estimated: float = 0.0  # Stage 5.5+ 에서 cost cap 검증

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_type": self.page_type.value,
            "problems": [p.to_dict() for p in self.problems],
            "needs_review": self.needs_review,
            "confidence": self.confidence,
            "raw_response": self.raw_response,
            "cost_usd_estimated": self.cost_usd_estimated,
        }


# ── needs_vlm_fallback 휴리스틱 ──


def needs_vlm_fallback(
    *,
    paper_type: str,
    tier1_required: bool,
    tier0_anchor_count: int,
    operating_problem_count: Optional[int] = None,
    page_role: str = "unknown",
    cross_page_duplicate_ratio: float = 0.0,
) -> tuple[bool, str]:
    """VLM 호출 필요 여부 휴리스틱 (실 호출은 Stage 5.5+ 영역).

    trigger 조건:
    - tier1_required (Tier 0 실패 — scanned PDF 등)
    - page_role == 'unknown' + tier0 anchor 0
    - paper_type unknown + tier0 over-detection (recall > 3x)
    - cross_page duplicate_ratio > 0.5 (학습자료 본문 폭증) — Tier 0 대신 VLM 검수 필요

    Returns:
        (need_vlm, reason).
    """
    if tier1_required:
        return (True, "tier1_required")
    if page_role == "unknown" and tier0_anchor_count == 0:
        return (True, "tier0_no_anchor")
    if paper_type == "unknown" and operating_problem_count and tier0_anchor_count > operating_problem_count * 3:
        return (True, "unknown_layout_overdetect")
    if cross_page_duplicate_ratio > 0.5:
        return (True, "duplicate_anchor_polution")
    return (False, "")


# ── JSON schema validator ──


_VALID_PAGE_TYPES = {pt.value for pt in VlmPageType}


class VlmSchemaError(Exception):
    """VLM 응답 schema 위반."""


def validate_vlm_response(payload: dict[str, Any]) -> VlmPageResponse:
    """VLM mock/실 응답 dict 를 VlmPageResponse 로 검증/파싱.

    위반 시 VlmSchemaError raise. dispatcher 가 호출 후 결과 검증에 사용.
    """
    if not isinstance(payload, dict):
        raise VlmSchemaError("response must be dict")

    # page_type
    pt = payload.get("page_type")
    if pt not in _VALID_PAGE_TYPES:
        raise VlmSchemaError(f"invalid page_type: {pt!r}")

    # problems
    problems_raw = payload.get("problems")
    if not isinstance(problems_raw, list):
        raise VlmSchemaError("problems must be list")

    problems: list[VlmProblemDetection] = []
    for i, p in enumerate(problems_raw):
        if not isinstance(p, dict):
            raise VlmSchemaError(f"problems[{i}] must be dict")
        try:
            number = int(p["number"])
        except (KeyError, TypeError, ValueError) as e:
            raise VlmSchemaError(f"problems[{i}].number invalid: {e}")
        if not (1 <= number <= 200):
            raise VlmSchemaError(f"problems[{i}].number out of range: {number}")

        bbox_raw = p.get("bbox")
        if not isinstance(bbox_raw, dict):
            raise VlmSchemaError(f"problems[{i}].bbox must be dict")
        try:
            bbox = VlmBbox(
                x=float(bbox_raw["x"]), y=float(bbox_raw["y"]),
                w=float(bbox_raw["w"]), h=float(bbox_raw["h"]),
                norm=bool(bbox_raw.get("norm", True)),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise VlmSchemaError(f"problems[{i}].bbox invalid: {e}")

        try:
            confidence = float(p.get("confidence", 0.0))
        except (TypeError, ValueError) as e:
            raise VlmSchemaError(f"problems[{i}].confidence invalid: {e}")
        if not (0.0 <= confidence <= 1.0):
            raise VlmSchemaError(f"problems[{i}].confidence out of [0,1]: {confidence}")

        problems.append(VlmProblemDetection(
            number=number, bbox=bbox, confidence=confidence,
            reason=str(p.get("reason", "")),
        ))

    needs_review = bool(payload.get("needs_review", False))
    confidence = float(payload.get("confidence", 0.0))
    cost_usd = float(payload.get("cost_usd_estimated", 0.0))

    return VlmPageResponse(
        page_type=VlmPageType(pt),
        problems=problems,
        needs_review=needs_review,
        confidence=confidence,
        raw_response=payload.get("raw_response", {}),
        cost_usd_estimated=cost_usd,
    )


# ── mock client ──


class MockVlmClient:
    """실 VLM API 대신 미리 정의된 응답 반환. 비용 0, 호출 0.

    사용법:
        client = MockVlmClient()
        client.set_response(page_number=3, response=VlmPageResponse(...))
        resp = client.detect_problems_for_page(request)

    Stage 5.5+ 에서 dispatcher 가 이 mock 으로 통합 검증 후 실 client 교체.
    """

    def __init__(self):
        self._responses: dict[int, VlmPageResponse] = {}
        self._call_log: list[VlmPageRequest] = []

    def set_response(self, page_number: int, response: VlmPageResponse) -> None:
        self._responses[page_number] = response

    def detect_problems_for_page(self, request: VlmPageRequest) -> VlmPageResponse:
        """page_number 기반 미리 설정된 mock 응답 반환. 없으면 빈 PROBLEM 응답."""
        self._call_log.append(request)
        if request.page_number in self._responses:
            return self._responses[request.page_number]
        return VlmPageResponse(
            page_type=VlmPageType.UNKNOWN,
            problems=[],
            needs_review=True,
            confidence=0.0,
            raw_response={"mock": True, "no_response_set": True},
            cost_usd_estimated=0.0,
        )

    @property
    def call_count(self) -> int:
        return len(self._call_log)

    @property
    def call_log(self) -> list[VlmPageRequest]:
        return list(self._call_log)


# ── proposal 통합 명세 (구현은 Stage 5.5+) ──
#
# VlmPageResponse → ProblemSegmentationProposal 변환 contract:
#
#   for problem in response.problems:
#       overlaps, iou, conflict = overlaps_existing_manual(
#           document_id=request.document_id,
#           candidate_bbox=problem.bbox.to_dict(),
#           page_number=request.page_number,
#       )
#       status = "rejected" if overlaps else (
#           "auto_passed" if problem.confidence >= 0.85 else "pending"
#       )
#       create_proposal(
#           tenant_id=...,
#           document_id=request.document_id,
#           page_number=request.page_number,
#           detected_problem_number=problem.number,
#           bbox=problem.bbox.to_dict(),
#           engine="vlm",
#           model_version="gemini-2.5-flash",
#           confidence=problem.confidence,
#           image_key=request.page_image_key,
#           raw_response={"reason": problem.reason, ...},
#           auto_status=status,
#       )
#
# 사용자 directive: VLM 결과 → ConfirmedProblem 직접 생성 절대 X.
# 학원장 검수 후 approve_proposal helper 만이 운영 풀로 승격.
