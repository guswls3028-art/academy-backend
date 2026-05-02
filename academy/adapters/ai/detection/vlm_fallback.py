# PATH: academy/adapters/ai/detection/vlm_fallback.py
"""VLM fallback adapter — low_conf 페이지 대상 1차/2차 분류기.

학원장 directive (2026-05-02): VLM은 메인 엔진 X, fallback only.
Phase 4 단계: 인터페이스만 설계. 실제 API 호출은 mock으로 대체.

Tier 구조 (가성비 우선):
  Tier 1 (free):       OpenCV + OCR + YOLO (이미 운영)
  Tier 2 (text-LLM):   GPT-5 nano text — page_role/anchor_role 결정 (OCR 결과 입력)
  Tier 3 (vision):     GPT-5 nano vision — bbox 추출 / 손글씨/그림 dominant 페이지
  Tier 4 (재시도):     GPT-5 mini retry
  Tier 5 (금지):       Sonnet/Opus/GPT-5.5/Haiku 등 비싼 모델 사용 X

호출 시점: low_conf_pages (paper_type_summary.low_conf_pages) 가 비지 않을 때만.
호출 비용 cap: doc당 max $5 (대략 GPT-5 nano vision ~500 페이지).

Public API:
  classify_page_role_text(ocr_blocks, page_meta) -> PageRoleResult  # Tier 2
  detect_problems_vision(image_path, page_meta) -> ProblemBboxResult  # Tier 3

본 모듈은 mock 구현부터 시작. 실제 OpenAI 호출은 별도 PR로 wire-up.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# ── 출력 schema ─────────────────────────────────────────────────


class PageRole(str, Enum):
    """VLM이 분류하는 페이지 역할 — 6분류."""

    COVER = "cover"             # 표지
    INDEX = "index"             # 목차
    PROBLEM = "problem"         # 문항
    EXPLANATION = "explanation" # 해설/본문
    ANSWER_KEY = "answer_key"   # 정답지
    MIXED = "mixed"             # 혼재 (문항 + 해설 등)


@dataclass
class PageRoleResult:
    """Tier 2 text-LLM 결과 — page_role + anchor_role 결정."""

    page_role: PageRole
    should_skip: bool                # 매치업 인덱싱 X (cover/index/explanation/answer_key)
    confidence: float                # 0.0 ~ 1.0
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProblemBbox:
    """단일 문항 bbox (픽셀 좌표)."""

    number: int                       # 문항 번호 (OCR 또는 VLM 추출)
    bbox: Tuple[int, int, int, int]   # (x, y, w, h)
    confidence: float                 # 0.0 ~ 1.0


@dataclass
class ProblemBboxResult:
    """Tier 3 vision 결과 — 페이지 내 문항 bbox 리스트."""

    page_role: PageRole
    should_skip: bool
    problems: List[ProblemBbox]
    confidence: float
    debug: Dict[str, Any] = field(default_factory=dict)


# ── Adapter protocol (mock + real 공통) ─────────────────────────


class VLMTextAdapter(Protocol):
    """Tier 2 text-LLM 어댑터 — page_role 분류."""

    def classify(
        self,
        *,
        ocr_text: str,
        ocr_blocks: List[Dict[str, Any]] | None = None,
        page_meta: Dict[str, Any] | None = None,
    ) -> PageRoleResult: ...


class VLMVisionAdapter(Protocol):
    """Tier 3 vision-VLM 어댑터 — bbox 추출."""

    def detect_problems(
        self,
        *,
        image_path: str,
        page_meta: Dict[str, Any] | None = None,
    ) -> ProblemBboxResult: ...


# ── Mock 구현 ───────────────────────────────────────────────────


class MockVLMTextAdapter:
    """Mock — keyword heuristic으로 page_role 추정.

    실제 OpenAI gpt-5-nano text 호출 wire-up 전까지 사용.
    출력 schema는 real adapter와 동일하므로 downstream code 영향 없음.
    """

    SKIP_KEYWORDS = {
        PageRole.COVER:       ("표지", "cover", "PROJECT", "시리즈"),
        PageRole.INDEX:       ("CONTENTS", "목차", "Part ", "Chapter ", "PART ", "CHAPTER "),
        PageRole.EXPLANATION: ("해설", "풀이", "정답 및 해설", "Step "),
        PageRole.ANSWER_KEY:  ("정답지", "정답표", "ANSWER KEY", "answers"),
    }

    def classify(
        self,
        *,
        ocr_text: str,
        ocr_blocks: List[Dict[str, Any]] | None = None,
        page_meta: Dict[str, Any] | None = None,
    ) -> PageRoleResult:
        text = (ocr_text or "")
        for role, kws in self.SKIP_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return PageRoleResult(
                    page_role=role,
                    should_skip=True,
                    confidence=0.7,
                    debug={"adapter": "mock", "matched_keywords": [
                        kw for kw in kws if kw in text
                    ]},
                )
        # 기본: 문항 페이지로 가정 (불확실)
        return PageRoleResult(
            page_role=PageRole.PROBLEM,
            should_skip=False,
            confidence=0.5,
            debug={"adapter": "mock", "matched_keywords": []},
        )


class MockVLMVisionAdapter:
    """Mock — 단일 page-as-problem bbox 반환.

    실제 OpenAI gpt-5-nano vision 호출 wire-up 전까지 사용.
    도메인 코드가 결과 schema에 의존해도 깨지지 않게 conservative default.
    """

    def detect_problems(
        self,
        *,
        image_path: str,
        page_meta: Dict[str, Any] | None = None,
    ) -> ProblemBboxResult:
        # 기존 page 메타에서 bbox 후보 사용 (있으면)
        meta = page_meta or {}
        boxes = meta.get("boxes") or []
        problems: List[ProblemBbox] = []
        for i, b in enumerate(boxes, start=1):
            # box format: (x, y, w, h)
            try:
                x, y, w, h = b[:4]
                problems.append(ProblemBbox(
                    number=i,
                    bbox=(int(x), int(y), int(w), int(h)),
                    confidence=0.5,
                ))
            except Exception:
                continue
        return ProblemBboxResult(
            page_role=PageRole.PROBLEM,
            should_skip=False,
            problems=problems,
            confidence=0.5,
            debug={"adapter": "mock", "fallback_to_existing_boxes": True},
        )


# ── Factory + 환경 변수 기반 선택 ───────────────────────────────


_TEXT_ADAPTER: Optional[VLMTextAdapter] = None
_VISION_ADAPTER: Optional[VLMVisionAdapter] = None


def get_text_adapter() -> VLMTextAdapter:
    """env MATCHUP_VLM_TEXT_ADAPTER 기반 어댑터 반환.

    값:
      "mock" (default) — MockVLMTextAdapter (현재 단계)
      "openai_gpt5_nano" — 실제 OpenAI 호출 (Phase 4 후속 PR에서 구현)
    """
    global _TEXT_ADAPTER
    if _TEXT_ADAPTER is None:
        choice = os.getenv("MATCHUP_VLM_TEXT_ADAPTER", "mock").lower()
        if choice == "mock":
            _TEXT_ADAPTER = MockVLMTextAdapter()
        else:
            logger.warning("Unknown text adapter %r, falling back to mock", choice)
            _TEXT_ADAPTER = MockVLMTextAdapter()
    return _TEXT_ADAPTER


def get_vision_adapter() -> VLMVisionAdapter:
    """env MATCHUP_VLM_VISION_ADAPTER 기반 어댑터 반환."""
    global _VISION_ADAPTER
    if _VISION_ADAPTER is None:
        choice = os.getenv("MATCHUP_VLM_VISION_ADAPTER", "mock").lower()
        if choice == "mock":
            _VISION_ADAPTER = MockVLMVisionAdapter()
        else:
            logger.warning("Unknown vision adapter %r, falling back to mock", choice)
            _VISION_ADAPTER = MockVLMVisionAdapter()
    return _VISION_ADAPTER


# 테스트/리셋용
def _reset_adapters() -> None:
    global _TEXT_ADAPTER, _VISION_ADAPTER
    _TEXT_ADAPTER = None
    _VISION_ADAPTER = None


# ── Public 진입점 ───────────────────────────────────────────────


def classify_page_role_text(
    *,
    ocr_text: str,
    ocr_blocks: List[Dict[str, Any]] | None = None,
    page_meta: Dict[str, Any] | None = None,
) -> PageRoleResult:
    """Tier 2 text-LLM 호출 — page_role 분류.

    호출 시점: low_conf 페이지 (page_confidence < 0.55) 만.
    """
    adapter = get_text_adapter()
    return adapter.classify(
        ocr_text=ocr_text,
        ocr_blocks=ocr_blocks,
        page_meta=page_meta,
    )


def detect_problems_vision(
    *,
    image_path: str,
    page_meta: Dict[str, Any] | None = None,
) -> ProblemBboxResult:
    """Tier 3 vision-VLM 호출 — bbox 추출.

    호출 시점: classify_page_role_text가 PROBLEM/MIXED + low confidence 일 때만.
    """
    adapter = get_vision_adapter()
    return adapter.detect_problems(
        image_path=image_path,
        page_meta=page_meta,
    )
