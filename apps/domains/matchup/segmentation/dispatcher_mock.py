"""Stage 5.7 — segmentation dispatcher mock integration (dry-run).

Tier 0 plateau (Stage 5.5.5) + fallback router dry-run (Stage 5.6) 후 — router
판정을 segmentation dispatcher 구조에 mock-only 로 wiring 한 prototype.

운영 dispatcher (`academy/adapters/ai/detection/segment_dispatcher.py`) 는 절대
변경하지 않음 — 본 모듈은 별도 mock entrypoint.

원칙 (사용자 directive Stage 5.7):
- 운영 DB write 0회
- TenantSegmentationProfile / LayoutFingerprint / ManualCorrectionDelta INSERT 0회
- MatchupProblem 수정 0회 / selected_problem_ids / manual=true 미접근
- ProblemSegmentationProposal INSERT 0회
- callback path 변경 0회 — `apps.domains.ai.gateway.dispatch_job` 호출 0회
- OCR/VLM 실 호출 0회 — mock request schema 만
- R2 write 0회 / paper_type 외부 응답 키 _internal_ 마킹 유지

Output schema 통일:
- 운영 `segment_questions_multipage` 출력과 동일 형식 (pages, total_boxes, is_pdf, tmp_dirs)
- + dispatcher-specific 메타 (route, route_decision, mock_*_request, validation_marks)

route 별 mock output:
- TIER0_SUFFICIENT       → Tier 0 candidates → pages.boxes/numbers (운영 형식)
- TIER1_OCR_REQUIRED     → pages 빈 list + mock_ocr_request (호출자가 OCR 결정)
- TIER2_VLM_REQUIRED     → pages 빈 list + mock_vlm_request (호출자가 VLM 결정)
- TIER2_VLM_HYBRID       → Tier 0 partial pages + mock_vlm_request (anchor 0 page 만)
- YOLO_FAST_PATH_CANDIDATE → pages 빈 marker + yolo_fast_path_marker=True (호출자가 YOLO 결정)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .fallback_router import (
    FallbackRouteDecision, decision_to_dict, route_fallback,
)
from .tier0_native_pdf import analyze_pdf_v5_4

SCHEMA_VERSION = "5.7-mock-1"


# ── Mock dispatcher output schema ────────────────────────────────────


@dataclass
class ValidationMarks:
    """dry-run 검증 — 모든 카운트 0 이어야 함."""
    operations_db_writes: int = 0
    proposal_inserts: int = 0
    callback_calls: int = 0
    real_ocr_calls: int = 0
    real_vlm_calls: int = 0
    r2_writes: int = 0
    matchup_problem_updates: int = 0
    selected_problem_ids_changes: int = 0


@dataclass
class MockDispatcherOutput:
    """단일 doc 의 mock dispatcher 결과.

    operations 의 `segment_questions_multipage` 와 호환 형식 + dispatcher-specific 메타.
    """
    schema_version: str
    route: str
    # 운영 호환 필드
    pages: list[dict] = field(default_factory=list)
    total_boxes: int = 0
    is_pdf: bool = True
    tmp_dirs: list[str] = field(default_factory=list)
    # router decision detail
    route_decision: dict = field(default_factory=dict)
    mock_ocr_request: Optional[dict] = None
    mock_vlm_request: Optional[dict] = None
    yolo_fast_path_marker: bool = False
    # mock 비용 집계
    cost_cap_summary: dict = field(default_factory=dict)
    # validation
    validation: ValidationMarks = field(default_factory=ValidationMarks)
    # debug
    debug: dict = field(default_factory=dict)


# ── 보조 ───────────────────────────────────────────────────────────


def _tier0_pages_to_dispatcher_pages(tier0_result: dict) -> tuple[list[dict], int]:
    """analyze_pdf_v5_4 의 page output 을 운영 dispatcher pages 형식으로 변환.

    운영 schema:
        page = {"page_index", "image_path", "boxes": [(x,y,w,h)], "numbers": [int|None],
                "has_embedded_text", "is_skip_page", "paper_type", "paper_type_debug"}

    Tier 0 의 bbox_norm 은 (x, y, w, h) 0~1 normalize. 운영은 픽셀 좌표.
    Tier 0 dry-run 이라 픽셀 변환 X — bbox_norm 그대로 두고 마커로 분리.
    """
    pages_out: list[dict] = []
    total_boxes = 0
    paper_type_internal = tier0_result.get("_internal_paper_type") or "unknown"
    for p in tier0_result.get("pages") or []:
        candidates = p.get("bbox_candidates") or []
        anchors = p.get("anchors") or []
        boxes_norm = [c.get("bbox_norm") for c in candidates]
        numbers: list[Optional[int]] = []
        for a, c in zip(anchors[:len(candidates)], candidates):
            numbers.append(a.get("number") if isinstance(a, dict) else None)
        # 부족하면 None 채움
        while len(numbers) < len(candidates):
            numbers.append(None)
        pages_out.append({
            "page_index": p.get("page_index", 0),
            "image_path": "",                         # mock — 실 image_path X
            "boxes_norm": boxes_norm,                  # 정규화 좌표 (mock 마커)
            "numbers": numbers,
            "has_embedded_text": p.get("has_embedded_text", False),
            "is_skip_page": p.get("role") in ("cover", "answer_key", "index"),
            "_internal_paper_type": paper_type_internal,  # 외부 노출 금지 (마킹 유지)
            "anchor_count": p.get("anchor_count", 0),
            "role": p.get("role", "unknown"),
        })
        total_boxes += len(boxes_norm)
    return pages_out, total_boxes


def _build_cost_summary(decision: FallbackRouteDecision) -> dict:
    summary: dict[str, Any] = {"engine": None, "estimated_units": 0, "estimated_total_usd": 0.0, "within_cap": True}
    if decision.mock_ocr_request and decision.mock_ocr_request.cost_cap:
        cap = decision.mock_ocr_request.cost_cap
        summary.update({
            "engine": cap.engine,
            "estimated_units": cap.estimated_units,
            "estimated_total_usd": cap.estimated_total_usd,
            "within_cap": cap.within_cap,
        })
    if decision.mock_vlm_request and decision.mock_vlm_request.cost_cap:
        cap = decision.mock_vlm_request.cost_cap
        summary.update({
            "engine": cap.engine,
            "estimated_units": cap.estimated_units,
            "estimated_total_usd": cap.estimated_total_usd,
            "within_cap": cap.within_cap,
        })
    return summary


# ── Mock dispatcher entry point ─────────────────────────────────────


def dispatch_mock(
    pdf_path: str,
    *,
    file_name: Optional[str] = None,
    profile: Optional[dict] = None,
    ocr_engine: str = "google_cloud_vision",
    vlm_engine: str = "gemini_vision",
    per_doc_usd_cap: float = 5.0,
) -> MockDispatcherOutput:
    """Tier 0 → fallback router → 5-route 별 mock output 생성.

    실 OCR/VLM 호출 / DB write / proposal INSERT / callback / R2 write 0회.

    Args:
        pdf_path: PDF 경로
        file_name: 분류용 파일명
        profile: tenant profile JSON (선택)
        ocr_engine / vlm_engine: mock label
        per_doc_usd_cap: doc 1개당 비용 cap (USD)

    Returns:
        MockDispatcherOutput — 운영 호환 schema + dispatcher 메타
    """
    # Tier 0 분석 (DB write 0회 / OCR/VLM 호출 0회)
    tier0 = analyze_pdf_v5_4(pdf_path, file_name=file_name, profile=profile)

    # router 분류
    decision = route_fallback(
        tier0, pdf_path=pdf_path, profile=profile,
        ocr_engine=ocr_engine, vlm_engine=vlm_engine,
        per_doc_usd_cap=per_doc_usd_cap,
    )

    out = MockDispatcherOutput(
        schema_version=SCHEMA_VERSION,
        route=decision.route,
        is_pdf=True,
        tmp_dirs=[],
        route_decision=decision_to_dict(decision),
        cost_cap_summary=_build_cost_summary(decision),
        validation=ValidationMarks(),
        debug={
            "tier0_version": tier0.get("version"),
            "page_count": tier0.get("page_count"),
            "text_pages": tier0.get("text_pages"),
            "_internal_paper_type": tier0.get("_internal_paper_type"),
        },
    )

    if decision.route == "TIER0_SUFFICIENT":
        pages, total = _tier0_pages_to_dispatcher_pages(tier0)
        out.pages = pages
        out.total_boxes = total

    elif decision.route == "TIER1_OCR_REQUIRED":
        # OCR 후 결과로 page list 채울 예정 — 현재는 빈 list + mock OCR request
        out.pages = []
        out.total_boxes = 0
        out.mock_ocr_request = (
            asdict(decision.mock_ocr_request) if decision.mock_ocr_request else None
        )

    elif decision.route == "TIER2_VLM_REQUIRED":
        # VLM 결과 받은 후 page list 채울 예정 — 현재 빈 list + mock VLM request
        out.pages = []
        out.total_boxes = 0
        out.mock_vlm_request = (
            asdict(decision.mock_vlm_request) if decision.mock_vlm_request else None
        )

    elif decision.route == "TIER2_VLM_HYBRID":
        # Tier 0 가진 page 는 그대로 + anchor 0 problem page 는 mock_vlm_request
        pages, total = _tier0_pages_to_dispatcher_pages(tier0)
        out.pages = pages
        out.total_boxes = total
        out.mock_vlm_request = (
            asdict(decision.mock_vlm_request) if decision.mock_vlm_request else None
        )

    elif decision.route == "YOLO_FAST_PATH_CANDIDATE":
        # 운영 dispatcher 의 segment_questions_multipage YOLO path 가 처리할 후보 marker
        # 본 mock dispatcher 는 YOLO 호출 X — 호출자가 결정
        out.pages = []
        out.total_boxes = 0
        out.yolo_fast_path_marker = True

    else:
        # default — 안전: Tier 0 결과 그대로
        pages, total = _tier0_pages_to_dispatcher_pages(tier0)
        out.pages = pages
        out.total_boxes = total

    return out


def output_to_dict(o: MockDispatcherOutput) -> dict[str, Any]:
    """MockDispatcherOutput → JSON 직렬화용 dict."""
    return {
        "schema_version": o.schema_version,
        "route": o.route,
        "pages": o.pages,
        "total_boxes": o.total_boxes,
        "is_pdf": o.is_pdf,
        "tmp_dirs": o.tmp_dirs,
        "route_decision": o.route_decision,
        "mock_ocr_request": o.mock_ocr_request,
        "mock_vlm_request": o.mock_vlm_request,
        "yolo_fast_path_marker": o.yolo_fast_path_marker,
        "cost_cap_summary": o.cost_cap_summary,
        "validation": asdict(o.validation),
        "debug": o.debug,
    }
