"""Stage 5.6 — Tier 0/1/2 fallback router (dry-run).

Tier 0 plateau (Stage 5.5.5) 인정 후, OCR/VLM fallback 분기 router 의 dry-run 설계.

원칙 (사용자 directive Stage 5.6):
- 운영 DB write 0회
- TenantSegmentationProfile / LayoutFingerprint / ManualCorrectionDelta INSERT 0회
- MatchupProblem 수정 0회 / selected_problem_ids / manual=true 미접근
- ProblemSegmentationProposal INSERT 0회
- callback path 변경 0회
- **OCR/VLM 실 호출 0회 — mock request/response only**
- R2 write 0회
- paper_type 외부 응답 키 _internal_ 마킹 유지
- 비용 cap mock — 실 비용 산정 X

route 4종:
- TIER0_SUFFICIENT — Tier 0 결과만으로 충분 (cand 충분 + sequence 연속)
- TIER1_OCR_REQUIRED — text layer 0/부족 (스캔 PDF, KakaoTalk 사진)
- TIER2_VLM_REQUIRED — anchor 부족 / 비정형 layout (manual scale 대비 cand 매우 적음)
- TIER2_VLM_HYBRID — Tier 0 cand 일부 + VLM 보조로 보강
- YOLO_FAST_PATH_CANDIDATE — known stable layout (profile match high confidence)
  ↳ 실제 YOLO 호출은 본 router 영역 X — 운영 dispatcher 가 처리

operations 미연결: 본 router 는 dry-run analyzer. 호출자가 따로 wiring 결정.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ── 분류 임계값 (조정 가능) ─────────────────────────────────────────────────
# Tier 0 anchor cand 비율 (cand / page_count) — 이 미만이면 anchor 부족 의심
_MIN_CAND_PER_PAGE = 0.25
# Tier 0 sequence_continuity 임계 — 이 이상이면 연속성 OK
_MIN_SEQUENCE_CONTINUITY = 0.5
# Profile match 판정 — sample_count + confidence_score
_MIN_PROFILE_SAMPLES = 500
_MIN_PROFILE_CONFIDENCE = 0.60
# YOLO fast path 후보 — 매우 안정적인 layout match
_YOLO_LAYOUT_CONFIDENCE = 0.80
# manual GT 대비 cand 비율 — 너무 낮으면 hybrid VLM
_MANUAL_TO_CAND_RATIO_VLM_HYBRID = 0.30  # cand < manual * 0.30 면 hybrid

# 비용 cap mock (USD, 실제 가격은 호출 시점 검증 필수)
_OCR_COST_PER_PAGE_USD = 0.0015          # Cloud Vision document_text_detection
_VLM_COST_PER_CALL_USD = 0.001875        # Gemini 1.5 Pro per image (대략)
_TESSERACT_COST_PER_PAGE_USD = 0.0       # 로컬 — 비용 없음 (CPU 시간 만)
_PER_DOC_USD_CAP = 5.0                    # doc 1개당 $5 cap (mock 기본값)
_PER_TENANT_DAILY_USD_CAP = 50.0          # tenant 1일 $50 cap (mock 기본값)


# ── 라우팅 결과 / mock request schema ───────────────────────────────────


@dataclass
class CostCapMock:
    """비용 cap mock — 실 호출 검증용 schema."""
    engine: str
    per_unit_usd: float                # per page (OCR) 또는 per call (VLM)
    estimated_units: int
    estimated_total_usd: float
    per_doc_cap_usd: float = _PER_DOC_USD_CAP
    within_cap: bool = True
    note: str = ""


@dataclass
class OcrMockRequest:
    """OCR mock request schema — 실제 SDK 호출 X."""
    engine: str                         # 'tesseract' | 'google_cloud_vision'
    pdf_path: str
    page_indices: list[int]             # OCR 대상 page (0-based)
    languages: list[str] = field(default_factory=lambda: ["kor", "eng"])
    document_type_hint: str = "scanned_problem_pdf"
    cost_cap: Optional[CostCapMock] = None
    note: str = ""


@dataclass
class VlmMockRequest:
    """VLM mock request schema — 실제 SDK 호출 X."""
    engine: str                         # 'gemini_vision'
    pdf_path: str
    page_indices: list[int]             # VLM 분석 대상 page
    prompt_template: str = (
        "다음 PDF 페이지에서 문제 번호 anchor 와 problem bbox 를 detect 하라. "
        "응답 schema: list[{number, bbox_norm: [x, y, w, h]}]. "
        "내부 라벨은 외부 노출 금지."
    )
    expected_response_schema: dict = field(
        default_factory=lambda: {
            "type": "list",
            "items": {
                "number": "int",
                "bbox_norm": "list[float, 4]",
                "confidence": "float",
            },
        },
    )
    cost_cap: Optional[CostCapMock] = None
    note: str = ""


@dataclass
class FallbackRouteDecision:
    """단일 doc 의 fallback 라우팅 판정."""
    route: str                          # TIER0_SUFFICIENT | TIER1_OCR_REQUIRED | ...
    reason: str
    confidence: float                   # 0.0~1.0 — 판정 자체의 확신
    tier0_summary: dict                  # tier1_required, cand_total, page_count, ...
    mock_ocr_request: Optional[OcrMockRequest] = None
    mock_vlm_request: Optional[VlmMockRequest] = None
    debug: dict = field(default_factory=dict)


# ── 보조 함수 ──────────────────────────────────────────────────────────


def _summarize_tier0(tier0_result: dict) -> dict:
    """analyze_pdf_v5_4 / v5_5 결과에서 라우팅에 필요한 통계만 추출."""
    pages = tier0_result.get("pages") or []
    cand_total = 0
    anchor_total = 0
    role_counts: dict[str, int] = {}
    for p in pages:
        cand_total += len(p.get("bbox_candidates") or [])
        anchor_total += p.get("anchor_count") or 0
        role = p.get("role") or "unknown"
        role_counts[role] = role_counts.get(role, 0) + 1
    cross = tier0_result.get("cross_page") or {}
    return {
        "version": tier0_result.get("version"),
        "page_count": tier0_result.get("page_count") or 0,
        "text_pages": tier0_result.get("text_pages") or 0,
        "tier1_required": tier0_result.get("tier1_required", False),
        "tier1_reason": tier0_result.get("tier1_reason", ""),
        "cand_total": cand_total,
        "anchor_total": anchor_total,
        "role_counts": role_counts,
        "sequence_continuity": cross.get("sequence_continuity", 0.0),
        "duplicates_dropped": cross.get("duplicates_dropped", 0),
        "_internal_paper_type": tier0_result.get("_internal_paper_type", ""),
        "layout_v2_type": (tier0_result.get("layout_v2") or {}).get("type", ""),
        "layout_v2_confidence": (tier0_result.get("layout_v2") or {}).get("confidence", 0.0),
        "profile_used": tier0_result.get("profile_used", False),
    }


def _problem_pages_count(t0: dict) -> int:
    """role='problem' 인 page 수 — cover/answer 제외해서 cand 비율 계산."""
    role_counts = t0.get("role_counts") or {}
    n = role_counts.get("problem", 0)
    if n == 0:
        # fallback — page_count - cover/answer/index
        n = max(
            t0["page_count"] - role_counts.get("cover", 0)
            - role_counts.get("answer_key", 0) - role_counts.get("index", 0),
            1,
        )
    return n


def _ocr_cost_cap(
    engine: str, page_count: int,
    per_doc_cap: float = _PER_DOC_USD_CAP,
) -> CostCapMock:
    if engine == "tesseract":
        per = _TESSERACT_COST_PER_PAGE_USD
    else:
        per = _OCR_COST_PER_PAGE_USD
    total = round(per * page_count, 4)
    return CostCapMock(
        engine=engine, per_unit_usd=per, estimated_units=page_count,
        estimated_total_usd=total, per_doc_cap_usd=per_doc_cap,
        within_cap=total <= per_doc_cap,
        note=f"OCR mock estimate — {engine} × {page_count} pages",
    )


def _vlm_cost_cap(
    engine: str, page_count: int,
    per_doc_cap: float = _PER_DOC_USD_CAP,
) -> CostCapMock:
    per = _VLM_COST_PER_CALL_USD
    total = round(per * page_count, 4)
    return CostCapMock(
        engine=engine, per_unit_usd=per, estimated_units=page_count,
        estimated_total_usd=total, per_doc_cap_usd=per_doc_cap,
        within_cap=total <= per_doc_cap,
        note=f"VLM mock estimate — {engine} × {page_count} pages",
    )


def _profile_match_strength(profile: Optional[dict], layout_type: str) -> float:
    """profile.layout_thresholds 에서 layout_type 매칭 강도 (0.0~1.0)."""
    if not profile or not isinstance(profile, dict):
        return 0.0
    confidence_score = profile.get("confidence_score") or 0.0
    samples_used = profile.get("samples_used") or 0
    lt = profile.get("layout_thresholds") or {}
    matched_samples = 0
    for key, block in lt.items():
        if isinstance(block, dict) and block.get("layout_type") == layout_type:
            matched_samples += block.get("sample_count") or 0
    if matched_samples == 0:
        return 0.0
    sample_strength = min(1.0, matched_samples / _MIN_PROFILE_SAMPLES)
    conf_strength = min(1.0, confidence_score / _MIN_PROFILE_CONFIDENCE)
    return min(1.0, (sample_strength + conf_strength) / 2)


# ── 라우터 본체 ───────────────────────────────────────────────────────


def route_fallback(
    tier0_result: dict,
    *,
    pdf_path: Optional[str] = None,
    profile: Optional[dict] = None,
    ocr_engine: str = "google_cloud_vision",
    vlm_engine: str = "gemini_vision",
    per_doc_usd_cap: float = _PER_DOC_USD_CAP,
) -> FallbackRouteDecision:
    """Tier 0 결과 + profile 기반 fallback 라우팅 판정 (dry-run).

    실 OCR/VLM 호출 절대 X. 호출자가 mock_request 를 받아 별도 wiring 결정.

    Args:
        tier0_result: analyze_pdf_v5_4 또는 v5_5 의 출력 dict
        pdf_path: PDF 경로 (mock request 에 기록)
        profile: tenant profile JSON (선택)
        ocr_engine: OCR 엔진 mock label
        vlm_engine: VLM 엔진 mock label
        per_doc_usd_cap: doc 1개당 cap (USD)

    Returns:
        FallbackRouteDecision — 라우팅 결정 + mock request schema
    """
    t0 = _summarize_tier0(tier0_result)
    page_count = t0["page_count"]
    cand_total = t0["cand_total"]
    pdf_path = pdf_path or tier0_result.get("pdf_path") or ""

    # 1. Text layer 부족 → TIER1_OCR
    if t0["tier1_required"]:
        all_pages = list(range(page_count))
        cap = _ocr_cost_cap(ocr_engine, page_count, per_doc_cap=per_doc_usd_cap)
        return FallbackRouteDecision(
            route="TIER1_OCR_REQUIRED",
            reason=f"tier1_required=True ({t0['tier1_reason']})",
            confidence=0.95,
            tier0_summary=t0,
            mock_ocr_request=OcrMockRequest(
                engine=ocr_engine, pdf_path=pdf_path,
                page_indices=all_pages, cost_cap=cap,
                note="full-doc OCR — text layer 0/부족",
            ),
            debug={"trigger": "tier1_required"},
        )

    # 2. Anchor 0 → TIER2_VLM (text 있으나 anchor 검출 실패)
    if cand_total == 0 and page_count > 0:
        all_pages = list(range(page_count))
        cap = _vlm_cost_cap(vlm_engine, page_count, per_doc_cap=per_doc_usd_cap)
        return FallbackRouteDecision(
            route="TIER2_VLM_REQUIRED",
            reason="cand=0 — text layer 있으나 anchor 0 (비정형 layout 의심)",
            confidence=0.85,
            tier0_summary=t0,
            mock_vlm_request=VlmMockRequest(
                engine=vlm_engine, pdf_path=pdf_path,
                page_indices=all_pages, cost_cap=cap,
                note="full-doc VLM — anchor 검출 실패",
            ),
            debug={"trigger": "no_anchors_with_text"},
        )

    # 3. Cand / problem_pages 비율 너무 낮음 → TIER2_VLM_HYBRID
    problem_pages = _problem_pages_count(t0)
    cand_per_problem_page = cand_total / max(1, problem_pages)
    if cand_per_problem_page < _MIN_CAND_PER_PAGE:
        # 어떤 page 가 anchor 0 인지 — 그 page 들만 VLM
        hybrid_pages: list[int] = []
        for p in tier0_result.get("pages") or []:
            if (p.get("anchor_count") or 0) == 0 and p.get("role") == "problem":
                hybrid_pages.append(p.get("page_index", 0))
        if not hybrid_pages:
            hybrid_pages = list(range(page_count))
        cap = _vlm_cost_cap(vlm_engine, len(hybrid_pages), per_doc_cap=per_doc_usd_cap)
        return FallbackRouteDecision(
            route="TIER2_VLM_HYBRID",
            reason=(
                f"cand_per_problem_page={cand_per_problem_page:.2f} "
                f"< {_MIN_CAND_PER_PAGE} — anchor 부족 page VLM 보조"
            ),
            confidence=0.75,
            tier0_summary=t0,
            mock_vlm_request=VlmMockRequest(
                engine=vlm_engine, pdf_path=pdf_path,
                page_indices=hybrid_pages, cost_cap=cap,
                note="anchor 0 problem page 만 VLM 보조",
            ),
            debug={
                "trigger": "low_cand_ratio",
                "cand_per_problem_page": round(cand_per_problem_page, 3),
                "problem_pages_count": problem_pages,
            },
        )

    # 4. YOLO fast path 후보 — profile 매칭 매우 강하고 sequence 연속 OK
    layout_type = t0["layout_v2_type"]
    profile_match = _profile_match_strength(profile, layout_type)
    seq = t0["sequence_continuity"]
    if (
        profile_match >= _YOLO_LAYOUT_CONFIDENCE
        and seq >= _MIN_SEQUENCE_CONTINUITY
        and cand_total > 0
    ):
        return FallbackRouteDecision(
            route="YOLO_FAST_PATH_CANDIDATE",
            reason=(
                f"profile_match={profile_match:.2f} ≥ {_YOLO_LAYOUT_CONFIDENCE} "
                f"+ seq={seq:.2f} ≥ {_MIN_SEQUENCE_CONTINUITY} — known stable layout"
            ),
            confidence=0.85,
            tier0_summary=t0,
            debug={
                "trigger": "stable_layout_match",
                "profile_match": round(profile_match, 3),
                "sequence_continuity": round(seq, 3),
            },
        )

    # 5. Default: Tier 0 만으로 충분
    return FallbackRouteDecision(
        route="TIER0_SUFFICIENT",
        reason=(
            f"cand={cand_total} (per_problem_page={cand_per_problem_page:.2f}) "
            f"+ seq={seq:.2f}"
        ),
        confidence=0.70,
        tier0_summary=t0,
        debug={
            "trigger": "tier0_baseline",
            "cand_per_problem_page": round(cand_per_problem_page, 3),
            "sequence_continuity": round(seq, 3),
            "profile_match": round(profile_match, 3) if 'profile_match' in locals() else None,
        },
    )


def decision_to_dict(d: FallbackRouteDecision) -> dict[str, Any]:
    """FallbackRouteDecision → JSON 직렬화용 dict (mock 객체 포함)."""
    out: dict[str, Any] = {
        "route": d.route,
        "reason": d.reason,
        "confidence": d.confidence,
        "tier0_summary": d.tier0_summary,
        "debug": d.debug,
    }
    if d.mock_ocr_request:
        out["mock_ocr_request"] = asdict(d.mock_ocr_request)
    if d.mock_vlm_request:
        out["mock_vlm_request"] = asdict(d.mock_vlm_request)
    return out
