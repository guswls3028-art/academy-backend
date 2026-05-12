"""Stage 6.3F-3 — 운영 VLM (ProblemBbox / ProblemBboxResult) → mock unified schema normalizer.

운영 academy.adapters.ai.detection.vlm_fallback 의 dataclass 정의:
    ProblemBbox        — number(int), bbox: (x, y, w, h) 픽셀, confidence(float),
                         shared_with(list[int])
    ProblemBboxResult  — page_role(PageRole), should_skip(bool),
                         problems(list[ProblemBbox]), confidence(float),
                         paper_type(str), debug(dict)

mock_response_integrator (Stage 5.8) 의 schema:
    VlmDetectedProblem — number(Optional[int]), bbox_norm: (x, y, w, h) norm 0~1,
                         confidence(float, default 0.80)
    VlmPageResult       — page_index(int), detected_problems(list)
    MockVlmResponse     — engine, pdf_path, pages, cost_actual_usd, is_mock

OCR (Stage 6.3F-2) 과 다른 점:
- bbox: 운영 VLM 은 corner 가 아니라 (x, y, w, h) 픽셀 (좌표계만 정규화)
- confidence: 운영도 surface (OCR 처럼 None fallback 불필요)
- page_role / should_skip / paper_type: 운영만 surface — debug 메타로 보존
- shared_with: 운영 묶음 보기 처리 (VLM 특화) — debug 메타로 보존

원칙 (사용자 directive Stage 6.3F-3):
- 실 VLM SDK 호출 0회
- DB write / Proposal INSERT / MatchupProblem / callback / R2 write 0회
- 운영 vlm_fallback module 직접 import 0회 (duck-type — number/bbox/confidence/
  shared_with 속성만)
- credential / signed URL / raw image / full text 미저장
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
    MockVlmResponse, UnifiedCandidate, VlmDetectedProblem, VlmPageResult,
    _vlm_response_to_unified,
)


SCHEMA_VERSION = "6.3F-3-vlm-normalizer-1"


# ── 핵심 변환 함수 ───────────────────────────────────────────


def normalize_pixel_xywh_to_norm(
    x: float, y: float, w: float, h: float,
    *,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    """pixel (x, y, w, h) → normalized (x, y, w, h) 0~1.

    OCR (corner) 와 달리 운영 VLM 은 이미 width/height 형식이므로 좌표계만 정규화.

    Raises:
        ValueError: page_width/height ≤ 0 / 음수 width/height / non-numeric inputs
    """
    if page_width <= 0 or page_height <= 0:
        raise ValueError(
            f"page_width and page_height must be > 0; "
            f"got page_width={page_width}, page_height={page_height}"
        )
    if w < 0 or h < 0:
        raise ValueError(
            f"width / height must be ≥ 0; got w={w}, h={h}"
        )
    try:
        return (
            float(x) / float(page_width),
            float(y) / float(page_height),
            float(w) / float(page_width),
            float(h) / float(page_height),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric bbox values: {exc}") from exc


def real_vlm_problem_to_mock(
    real_problem: Any,
    *,
    page_width: float,
    page_height: float,
) -> VlmDetectedProblem:
    """운영 ProblemBbox duck-typed input → mock VlmDetectedProblem.

    Args:
        real_problem: number / bbox: (x,y,w,h) / confidence (필수) /
                      shared_with (선택) 속성 가진 객체.
        page_width / page_height: 픽셀 → norm 변환 분모.

    Returns:
        VlmDetectedProblem — bbox_norm (x,y,w,h) 0~1 + number + confidence (운영
        surface 그대로 보존, OCR 와 달리 None fallback 불필요).
    """
    number = getattr(real_problem, "number", None)
    bbox = getattr(real_problem, "bbox", None)
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        raise ValueError(
            f"real_problem.bbox must be 4-tuple/list (x,y,w,h); got {bbox!r}"
        )
    x, y, w, h = bbox
    bbox_norm = normalize_pixel_xywh_to_norm(
        x, y, w, h, page_width=page_width, page_height=page_height,
    )
    # 운영 confidence — float (surface 그대로). 누락 시 0.0 (semantic: 미상)
    raw_conf = getattr(real_problem, "confidence", None)
    try:
        confidence = float(raw_conf) if raw_conf is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0

    # number — int 0 (운영 default 'unknown') → Optional[int] None 으로 정규화
    # mock VlmDetectedProblem 의 number 가 Optional[int] 이므로 의미 보존.
    norm_number: Optional[int]
    if number is None:
        norm_number = None
    else:
        try:
            norm_number = int(number)
            if norm_number == 0:
                norm_number = None    # 운영 0='unknown' → mock None 매핑
        except (TypeError, ValueError):
            norm_number = None

    return VlmDetectedProblem(
        number=norm_number,
        bbox_norm=bbox_norm,
        confidence=confidence,
    )


def real_vlm_result_to_mock_response(
    real_result: Any,
    *,
    page_index: int,
    page_width: float,
    page_height: float,
    pdf_path: str = "",
    engine: str = "gemini_vision",
) -> MockVlmResponse:
    """운영 ProblemBboxResult duck-typed input → MockVlmResponse.

    page_index 는 caller 가 외부에서 주입 (운영 ProblemBboxResult 미surface — OCR 와 동일).

    debug 보존 (운영 VLM 특화 필드):
        page_role / should_skip / paper_type / shared_with — debug 안에 mirror.

    Args:
        real_result: page_role / should_skip / problems / confidence / paper_type /
                     debug 속성 가진 객체 (duck-type).
        page_index: 외부 주입.
        page_width / page_height: 픽셀 → norm 변환 분모 (caller 책임).
        engine: mock label.

    Returns:
        MockVlmResponse — is_mock=False (변환 결과, synthetic 아님).
    """
    problems_raw = getattr(real_result, "problems", []) or []
    detected: list[VlmDetectedProblem] = []
    for p in problems_raw:
        detected.append(real_vlm_problem_to_mock(
            p, page_width=page_width, page_height=page_height,
        ))

    # 운영 VLM debug 메타 보존
    real_debug = getattr(real_result, "debug", None) or {}
    page_meta_debug = {
        "page_role": str(getattr(real_result, "page_role", "")),
        "should_skip": bool(getattr(real_result, "should_skip", False)),
        "paper_type": str(getattr(real_result, "paper_type", "unknown")),
        "page_confidence": float(getattr(real_result, "confidence", 0.0)),
        "real_debug": dict(real_debug) if isinstance(real_debug, dict) else {},
        "shared_with_per_problem": [
            list(getattr(p, "shared_with", []) or []) for p in problems_raw
        ],
    }

    page_result = VlmPageResult(
        page_index=int(page_index),
        detected_problems=detected,
    )

    response = MockVlmResponse(
        engine=engine,
        pdf_path=pdf_path,
        pages=[page_result],
        cost_actual_usd=0.0,
        is_mock=False,    # 운영 변환 결과
    )
    # MockVlmResponse 자체에 debug field 없음 — page_meta_debug 는 caller 가 활용
    # (UnifiedCandidate.debug 로 propagate). 다만 raw response 직렬화 시 보존을 위해
    # 임시로 attribute 부여 (dataclass 확장 X — 동적 attribute).
    setattr(response, "real_page_meta", page_meta_debug)
    return response


def real_vlm_result_to_unified_candidates(
    real_result: Any,
    *,
    page_index: int,
    page_width: float,
    page_height: float,
) -> list[UnifiedCandidate]:
    """운영 ProblemBboxResult → UnifiedCandidate list (mock generator 동등 출력).

    UnifiedCandidate.source = "vlm".
    debug 에 운영 페이지 메타 (page_role / paper_type / shared_with) 보존.
    """
    response = real_vlm_result_to_mock_response(
        real_result,
        page_index=page_index,
        page_width=page_width,
        page_height=page_height,
    )
    candidates = _vlm_response_to_unified(response)
    # 운영 page meta 를 candidate debug 에 promote
    page_meta = getattr(response, "real_page_meta", {})
    if page_meta:
        shared = page_meta.get("shared_with_per_problem") or []
        for i, c in enumerate(candidates):
            if not c.debug:
                c.debug = {}
            c.debug["page_role"] = page_meta.get("page_role")
            c.debug["should_skip"] = page_meta.get("should_skip")
            c.debug["paper_type"] = page_meta.get("paper_type")
            if i < len(shared):
                c.debug["shared_with"] = shared[i]
    return candidates


def real_vlm_problems_to_unified_candidates(
    real_problems: Iterable[Any],
    *,
    page_index: int,
    page_width: float,
    page_height: float,
) -> list[UnifiedCandidate]:
    """ProblemBbox list 만 받는 lighter API (ProblemBboxResult wrap 없을 때).

    page_role / paper_type / shared_with 등 페이지 메타 없음 — 단순 problem list 변환.
    """
    detected = [
        real_vlm_problem_to_mock(p, page_width=page_width, page_height=page_height)
        for p in real_problems
    ]
    response = MockVlmResponse(
        engine="gemini_vision",
        pdf_path="",
        pages=[VlmPageResult(page_index=int(page_index), detected_problems=detected)],
        cost_actual_usd=0.0,
        is_mock=False,
    )
    return _vlm_response_to_unified(response)
