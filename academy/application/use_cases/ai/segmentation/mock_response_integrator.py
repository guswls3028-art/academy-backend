"""Stage 5.8 — Mock OCR/VLM response integration (dry-run).

Stage 5.7 mock dispatcher 가 mock request 만 만들었다면, Stage 5.8 은 mock response 까지
받아 Tier 0 candidates 와 합쳐 unified candidate / future proposal payload 를 만든다.

원칙 (사용자 directive Stage 5.8):
- 운영 DB write 0회
- ProblemSegmentationProposal **INSERT 절대 X** — payload schema 검증만
- TenantSegmentationProfile / LayoutFingerprint / ManualCorrectionDelta INSERT 0회
- MatchupProblem 수정 0회 / selected_problem_ids / manual=true 미접근
- callback path 변경 0회 (`apps.domains.ai.gateway.dispatch_job` 호출 0회)
- **OCR/VLM 실 호출 0회** — mock response 는 synthetic data
- R2 write 0회
- paper_type 외부 응답 키 _internal_ 마킹 유지
- manual_overlap validator 는 **DB query 없는 static mock** (실 manual=true row 미접근)

Output schema:
- UnifiedCandidate: 단일 후보 bbox (source 표시)
- ProposalPayloadCandidate: 미래 ProblemSegmentationProposal payload — INSERT X, schema 검증만
- UnifiedDispatcherOutput: dispatcher 결과 + unified candidates + proposal_payloads + validation
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from academy.domain.ai.segmentation_contracts import (
    MockOcrResponse,
    MockVlmResponse,
    OcrPageResult,
    OcrTextBlock,
    ProposalPayloadCandidate,
    UnifiedCandidate,
    ValidationError,
    VlmDetectedProblem,
    VlmPageResult,
    _bbox_iou_norm,
    _ocr_response_to_unified,
    _vlm_response_to_unified,
)

from .dispatcher_mock import (
    MockDispatcherOutput, ValidationMarks, dispatch_mock,
)


SCHEMA_VERSION = "5.8-mock-1"


# ── Unified dispatch output schema ───────────────────────────────────


@dataclass
class UnifiedDispatcherOutput:
    """Stage 5.8 unified output — dispatcher route 결과 + unified candidates + proposal payload."""
    schema_version: str
    route: str
    sources_used: list[str]                     # ["tier0"] / ["ocr"] / ["vlm"] / ["tier0", "vlm"] / ["yolo_marker"]
    unified_candidates: list[UnifiedCandidate] = field(default_factory=list)
    proposal_payloads: list[ProposalPayloadCandidate] = field(default_factory=list)
    cost_actual_usd: float = 0.0
    validation: ValidationMarks = field(default_factory=ValidationMarks)
    debug: dict = field(default_factory=dict)


# ── Mock response generator (synthetic, 실 호출 X) ─────────────────────


_DEFAULT_OCR_TEXT_BLOCKS_PER_PAGE = 8
_DEFAULT_VLM_PROBLEMS_PER_PAGE = 3


def make_mock_ocr_response(
    pdf_path: str, page_indices: list[int],
    *, engine: str = "google_cloud_vision",
    blocks_per_page: int = _DEFAULT_OCR_TEXT_BLOCKS_PER_PAGE,
) -> MockOcrResponse:
    """synthetic mock OCR response — 실 OCR API 호출 X."""
    pages: list[OcrPageResult] = []
    for p_idx in page_indices:
        blocks: list[OcrTextBlock] = []
        for i in range(blocks_per_page):
            # 좌측 column 가정 — y 분포 균등
            y = 0.10 + 0.10 * i
            if y > 0.95:
                break
            blocks.append(OcrTextBlock(
                bbox_norm=(0.10, round(y, 3), 0.80, 0.08),
                text=f"{i + 1}. mock OCR text block",
                confidence=0.85,
            ))
        pages.append(OcrPageResult(page_index=p_idx, text_blocks=blocks))
    return MockOcrResponse(
        engine=engine, pdf_path=pdf_path, page_count=len(page_indices),
        pages=pages, cost_actual_usd=0.0, is_mock=True,
    )


def make_mock_vlm_response(
    pdf_path: str, page_indices: list[int],
    *, engine: str = "gemini_vision",
    problems_per_page: int = _DEFAULT_VLM_PROBLEMS_PER_PAGE,
) -> MockVlmResponse:
    """synthetic mock VLM response — 실 VLM API 호출 X."""
    pages: list[VlmPageResult] = []
    for p_idx in page_indices:
        problems: list[VlmDetectedProblem] = []
        for i in range(problems_per_page):
            y = 0.12 + (0.85 / max(1, problems_per_page)) * i
            if y > 0.92:
                break
            problems.append(VlmDetectedProblem(
                number=i + 1, bbox_norm=(0.08, round(y, 3), 0.84, 0.22),
                confidence=0.80,
            ))
        pages.append(VlmPageResult(page_index=p_idx, detected_problems=problems))
    return MockVlmResponse(
        engine=engine, pdf_path=pdf_path,
        pages=pages, cost_actual_usd=0.0, is_mock=True,
    )


# ── Manual overlap mock validator (DB query 없는 static check) ────────


def manual_overlap_mock_validator(
    candidates: list[UnifiedCandidate],
    *,
    static_manual_bboxes: Optional[list[dict]] = None,
    iou_threshold: float = 0.30,
) -> dict[int, list[ValidationError]]:
    """static manual bbox 와 candidate 의 overlap 계산 — **DB query 0회**.

    real ProblemSegmentationProposal 의 manual_overlap 검증과 동일 logic 이지만,
    실 manual=True row 는 fetch 하지 않음. 호출자가 static_manual_bboxes 직접 주입.

    Returns:
        {candidate_index: [ValidationError]} — overlap 발견된 candidate 만
    """
    static_manual_bboxes = static_manual_bboxes or []
    errors_by_idx: dict[int, list[ValidationError]] = {}
    for idx, c in enumerate(candidates):
        for m in static_manual_bboxes:
            if m.get("page_index") != c.page_index:
                continue
            mb = m.get("bbox_norm")
            if not isinstance(mb, (list, tuple)) or len(mb) != 4:
                continue
            iou = _bbox_iou_norm(c.bbox_norm, tuple(mb))
            if iou >= iou_threshold:
                errors_by_idx.setdefault(idx, []).append(ValidationError(
                    code="manual_overlap",
                    detail=f"overlap with static manual bbox (mock) IoU={iou:.3f}",
                    bbox_iou=round(iou, 3),
                ))
                break
    return errors_by_idx


def _tier0_pages_to_unified(
    dispatcher_pages: list[dict],
) -> list[UnifiedCandidate]:
    """dispatcher_mock 의 pages (boxes_norm + numbers) → unified candidate."""
    out: list[UnifiedCandidate] = []
    for p in dispatcher_pages:
        page_idx = p.get("page_index", 0)
        boxes = p.get("boxes_norm") or []
        numbers = p.get("numbers") or []
        for i, b in enumerate(boxes):
            n = numbers[i] if i < len(numbers) else None
            try:
                bn = tuple(float(x) for x in b)
            except (TypeError, ValueError):
                continue
            if len(bn) != 4:
                continue
            out.append(UnifiedCandidate(
                page_index=page_idx, bbox_norm=bn, number=n,
                source="tier0", confidence=0.85,
                debug={"role": p.get("role")},
            ))
    return out


def _engine_for_source(source: str) -> str:
    return {
        "tier0": "native_pdf",
        "ocr": "ocr",
        "vlm": "vlm",
        "yolo": "yolo",
    }.get(source, "native_pdf")


def _to_proposal_payload(
    c: UnifiedCandidate, *,
    tenant_id: int, document_id: int,
    analysis_version_key: str,
    extra_errors: Optional[list[ValidationError]] = None,
) -> ProposalPayloadCandidate:
    """UnifiedCandidate → ProposalPayloadCandidate.

    Stage 6.3F-2 (A안 + B-style 마킹):
    - UnifiedCandidate.confidence None ("정보 없음") 일 때 ProposalPayloadCandidate.confidence
      는 0.0 으로 변환 (DB FloatField 호환 — `models.FloatField(default=0.0)` NOT NULL).
    - 단 raw_response 에 confidence_missing=True / confidence_strategy="missing_to_zero_for_db_compat"
      / source_confidence=None 마킹 → audit / debug 가 "낮음" 과 "정보 없음" 구분 가능.
    - UI 노출 정책: raw confidence 노출 X (PROPOSAL_REVIEW_UI_WIREFRAME 정의 — high/medium/low 추상화).
    """
    bx, by, bw, bh = c.bbox_norm
    status = "pending"
    errors = list(extra_errors or [])
    if any(e.code == "manual_overlap" for e in errors):
        status = "rejected"

    # confidence None 처리 (A안 + audit 마킹)
    raw_response = dict(c.debug)
    if c.confidence is None:
        confidence_db = 0.0
        raw_response["confidence_missing"] = True
        raw_response["confidence_strategy"] = "missing_to_zero_for_db_compat"
        raw_response["source_confidence"] = None
        raw_response.setdefault(
            "TODO_ranking_interpretation",
            "confidence=0 here means INFO_MISSING, not LOW. Ranking layer must "
            "treat differently — see PROPOSAL_NUMBER_NAMESPACE_POLICY follow-up.",
        )
    else:
        confidence_db = float(c.confidence)
        raw_response.setdefault("confidence_missing", False)
        raw_response.setdefault("source_confidence", float(c.confidence))

    return ProposalPayloadCandidate(
        tenant_id=tenant_id, document_id=document_id,
        page_number=c.page_index,
        detected_problem_number=int(c.number) if c.number else 0,
        bbox={
            "x": round(float(bx), 4), "y": round(float(by), 4),
            "w": round(float(bw), 4), "h": round(float(bh), 4),
            "norm": True,
        },
        engine=_engine_for_source(c.source),
        model_version="",
        confidence=round(confidence_db, 4),
        status=status,
        analysis_version_key=analysis_version_key,
        image_key="",
        raw_response=raw_response,
        validation_errors=errors,
    )


# ── Top-level integrator ─────────────────────────────────────────────


def integrate_responses(
    dispatcher_output: MockDispatcherOutput,
    *,
    mock_ocr_response: Optional[MockOcrResponse] = None,
    mock_vlm_response: Optional[MockVlmResponse] = None,
    tenant_id: int = 0,
    document_id: int = 0,
    analysis_version_key: str = "",
    static_manual_bboxes: Optional[list[dict]] = None,
) -> UnifiedDispatcherOutput:
    """5-route 별 dispatcher output + mock responses → unified candidates.

    Args:
        dispatcher_output: dispatch_mock 결과
        mock_ocr_response: TIER1_OCR 일 때 사용 (synthetic, 실 호출 X)
        mock_vlm_response: TIER2_VLM / HYBRID 일 때 사용 (synthetic, 실 호출 X)
        tenant_id / document_id: proposal payload 식별자 (실 INSERT X)
        static_manual_bboxes: manual_overlap 검증용 static bbox list — DB query 없음

    Returns:
        UnifiedDispatcherOutput — unified_candidates + proposal_payloads + ValidationMarks
    """
    route = dispatcher_output.route
    sources: list[str] = []
    candidates: list[UnifiedCandidate] = []
    cost_actual = 0.0

    if route == "TIER0_SUFFICIENT":
        candidates.extend(_tier0_pages_to_unified(dispatcher_output.pages))
        if candidates:
            sources.append("tier0")

    elif route == "TIER1_OCR_REQUIRED":
        if mock_ocr_response is not None:
            candidates.extend(_ocr_response_to_unified(mock_ocr_response))
            cost_actual = mock_ocr_response.cost_actual_usd
            sources.append("ocr")

    elif route == "TIER2_VLM_REQUIRED":
        if mock_vlm_response is not None:
            candidates.extend(_vlm_response_to_unified(mock_vlm_response))
            cost_actual = mock_vlm_response.cost_actual_usd
            sources.append("vlm")

    elif route == "TIER2_VLM_HYBRID":
        # Tier 0 가진 page candidate + mock VLM 보조 (anchor 0 page)
        candidates.extend(_tier0_pages_to_unified(dispatcher_output.pages))
        if candidates:
            sources.append("tier0")
        if mock_vlm_response is not None:
            candidates.extend(_vlm_response_to_unified(mock_vlm_response))
            cost_actual = mock_vlm_response.cost_actual_usd
            sources.append("vlm")

    elif route == "YOLO_FAST_PATH_CANDIDATE":
        # 운영 dispatcher YOLO path 가 처리할 후보 — 본 integrator 는 marker 만
        sources.append("yolo_marker")

    else:
        candidates.extend(_tier0_pages_to_unified(dispatcher_output.pages))
        if candidates:
            sources.append("tier0")

    # manual_overlap mock validation
    overlap_errors = manual_overlap_mock_validator(
        candidates, static_manual_bboxes=static_manual_bboxes,
    )

    # proposal payload 생성 (INSERT 안 함, schema validation 만)
    payloads: list[ProposalPayloadCandidate] = []
    for idx, c in enumerate(candidates):
        errors = overlap_errors.get(idx)
        payload = _to_proposal_payload(
            c, tenant_id=tenant_id, document_id=document_id,
            analysis_version_key=analysis_version_key,
            extra_errors=errors,
        )
        payloads.append(payload)

    return UnifiedDispatcherOutput(
        schema_version=SCHEMA_VERSION,
        route=route,
        sources_used=sources,
        unified_candidates=candidates,
        proposal_payloads=payloads,
        cost_actual_usd=cost_actual,
        validation=ValidationMarks(),  # 모든 field 0 유지
        debug={
            "tier0_pages": len(dispatcher_output.pages),
            "tier0_total_boxes": dispatcher_output.total_boxes,
            "yolo_fast_path_marker": dispatcher_output.yolo_fast_path_marker,
            "ocr_pages": len(mock_ocr_response.pages) if mock_ocr_response else 0,
            "vlm_pages": len(mock_vlm_response.pages) if mock_vlm_response else 0,
            "manual_overlap_count": sum(len(v) for v in overlap_errors.values()),
        },
    )


def integrate_full_dryrun(
    pdf_path: str,
    *,
    file_name: Optional[str] = None,
    profile: Optional[dict] = None,
    tenant_id: int = 0,
    document_id: int = 0,
    analysis_version_key: str = "",
    static_manual_bboxes: Optional[list[dict]] = None,
    ocr_engine: str = "google_cloud_vision",
    vlm_engine: str = "gemini_vision",
) -> tuple[MockDispatcherOutput, UnifiedDispatcherOutput]:
    """end-to-end dry-run: dispatch_mock → mock OCR/VLM response 생성 → integrate.

    실 OCR/VLM 호출 0회 — synthetic response 만.
    """
    dispatcher = dispatch_mock(
        pdf_path, file_name=file_name, profile=profile,
        ocr_engine=ocr_engine, vlm_engine=vlm_engine,
    )

    mock_ocr: Optional[MockOcrResponse] = None
    mock_vlm: Optional[MockVlmResponse] = None
    if dispatcher.route == "TIER1_OCR_REQUIRED" and dispatcher.mock_ocr_request:
        page_indices = list(dispatcher.mock_ocr_request.get("page_indices") or [])
        mock_ocr = make_mock_ocr_response(
            pdf_path, page_indices, engine=ocr_engine,
        )
    elif dispatcher.route in ("TIER2_VLM_REQUIRED", "TIER2_VLM_HYBRID") and dispatcher.mock_vlm_request:
        page_indices = list(dispatcher.mock_vlm_request.get("page_indices") or [])
        mock_vlm = make_mock_vlm_response(
            pdf_path, page_indices, engine=vlm_engine,
        )

    unified = integrate_responses(
        dispatcher,
        mock_ocr_response=mock_ocr, mock_vlm_response=mock_vlm,
        tenant_id=tenant_id, document_id=document_id,
        analysis_version_key=analysis_version_key,
        static_manual_bboxes=static_manual_bboxes,
    )
    return dispatcher, unified


def unified_to_dict(o: UnifiedDispatcherOutput) -> dict[str, Any]:
    """UnifiedDispatcherOutput → JSON 직렬화용 dict."""
    return {
        "schema_version": o.schema_version,
        "route": o.route,
        "sources_used": o.sources_used,
        "unified_candidates": [
            {
                "page_index": c.page_index,
                "bbox_norm": list(c.bbox_norm),
                "number": c.number, "source": c.source,
                "confidence": c.confidence, "debug": c.debug,
            }
            for c in o.unified_candidates
        ],
        "proposal_payloads": [
            {
                **{k: v for k, v in asdict(p).items() if k != "validation_errors"},
                "validation_errors": [asdict(e) for e in p.validation_errors],
            }
            for p in o.proposal_payloads
        ],
        "cost_actual_usd": o.cost_actual_usd,
        "validation": asdict(o.validation),
        "debug": o.debug,
    }
