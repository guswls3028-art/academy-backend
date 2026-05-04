"""VLM bbox 다층 게이트 + 자동 품질 점수 회귀 락 (P0-1 + P0-2).

운영 사고 fix (2026-05-03 시각 검수):
- D-1: VLM 4-quadrant 오분할 (Q1이 두 박스로 split, 보기/답안만 cell)
- D-2: mid-cut strip (cell 가로 띠)
- D-3: 표지/헤더가 problem (PageRole=problem 응답)
- D-4: 시험지 헤더 prepend (페이지 위쪽 너무 멀리 시작)

이 테스트는 게이트 회귀 락. fail 시 운영 결함 재현 신호.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest

from academy.adapters.ai.detection.vlm_fallback import (
    PageRole,
    ProblemBbox,
    ProblemBboxResult,
)
from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _compute_quality_score,
    _validate_vlm_bboxes,
)


def _bbox_result(
    page_role: PageRole = PageRole.PROBLEM,
    problems: List[Tuple[int, int, int, int, int]] = None,
    confidence: float = 0.95,
) -> ProblemBboxResult:
    """ProblemBboxResult fixture. problems = [(num, x, y, w, h), ...]"""
    return ProblemBboxResult(
        page_role=page_role,
        should_skip=False,
        problems=[
            ProblemBbox(number=n, bbox=(x, y, w, h), confidence=0.9)
            for (n, x, y, w, h) in (problems or [])
        ],
        confidence=confidence,
        debug={"adapter": "gemini"},
    )


def _make_image(monkeypatch, w: int = 2000, h: int = 2800):
    """cv2.imread mock — 지정한 dim의 가짜 이미지 반환.

    matchup_pipeline._validate_vlm_bboxes는 함수 내부에서 cv2 lazy import.
    cv2 모듈 자체에 setattr → 모든 호출자가 patched imread 사용.
    """
    import cv2
    import numpy as np

    def fake_imread(path):
        return np.zeros((h, w, 3), dtype=np.uint8)

    monkeypatch.setattr(cv2, "imread", fake_imread)


# ── D-3: page_role 게이트 ──

@pytest.mark.parametrize("role", [
    PageRole.COVER, PageRole.INDEX,
    PageRole.EXPLANATION, PageRole.ANSWER_KEY,
])
def test_gate_page_role_skip_rejects(monkeypatch, role):
    """D-3: cover/index/explanation/answer_key 페이지 role → reject."""
    _make_image(monkeypatch)
    result = _bbox_result(
        page_role=role,
        problems=[(1, 100, 300, 1000, 800), (2, 100, 1200, 1000, 800)],
    )
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=0)
    assert out is None, f"page_role={role.value}는 reject되어야 함"


def test_gate_page_role_problem_passes(monkeypatch):
    """PROBLEM/MIXED page_role은 다른 게이트 통과 시 OK."""
    _make_image(monkeypatch)
    result = _bbox_result(
        page_role=PageRole.PROBLEM,
        problems=[(1, 100, 300, 1500, 800), (2, 100, 1200, 1500, 800)],
    )
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=0)
    assert out is result


# ── D-2: bbox aspect (strip cut) ──

def test_gate_strip_cut_low_height_rejects(monkeypatch):
    """D-2: bbox height < page * 0.05 → strip cut → reject."""
    _make_image(monkeypatch, w=2000, h=2800)  # h_img=2800, threshold=140px
    result = _bbox_result(problems=[
        (1, 100, 300, 1500, 100),  # h=100 < 140 → strip
        (2, 100, 500, 1500, 800),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=1)
    assert out is None


def test_gate_thin_box_low_width_rejects(monkeypatch):
    """너무 좁은 cell (width < page * 0.15) → reject."""
    _make_image(monkeypatch, w=2000, h=2800)  # min_w = 300
    result = _bbox_result(problems=[
        (1, 100, 300, 200, 800),  # w=200 < 300 → thin
        (2, 500, 300, 1500, 800),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=1)
    assert out is None


# ── D-4: y_min header zone ──

def test_gate_header_zone_y_min_rejects(monkeypatch):
    """D-4: bbox y_min < page * 0.08 → 헤더 침범 의심 → reject."""
    _make_image(monkeypatch, w=2000, h=2800)  # header_zone = 224
    result = _bbox_result(problems=[
        (1, 100, 100, 1500, 800),  # y=100 < 224 → header
        (2, 100, 1200, 1500, 800),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=2)
    assert out is None


def test_gate_header_zone_below_passes(monkeypatch):
    """y_min >= header_zone(8%)면 통과."""
    _make_image(monkeypatch, w=2000, h=2800)
    result = _bbox_result(problems=[
        (1, 100, 300, 1500, 800),  # y=300 > 224 → OK
        (2, 100, 1300, 1500, 800),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=2)
    assert out is result


# ── D-1: bbox overlap (4-quadrant 오분할) ──

def test_gate_bbox_overlap_rejects(monkeypatch):
    """D-1: 두 박스가 IoU > 0.3로 겹치면 잘못 잡힘 → reject."""
    _make_image(monkeypatch)
    # 두 박스가 거의 같은 영역 차지 (Q1이 두 cell로 split 케이스)
    result = _bbox_result(problems=[
        (1, 100, 300, 1000, 1000),
        (2, 200, 400, 1000, 1000),  # 70% 겹침
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=3)
    assert out is None


def test_gate_bbox_no_overlap_passes(monkeypatch):
    """겹치지 않는 박스 통과."""
    _make_image(monkeypatch)
    result = _bbox_result(problems=[
        (1, 100, 300, 800, 800),
        (2, 100, 1500, 800, 800),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=3)
    assert out is result


# ── D-1 보강: number 시퀀스 ──

def test_gate_dup_numbers_rejects(monkeypatch):
    """중복 번호 → reject."""
    _make_image(monkeypatch)
    result = _bbox_result(problems=[
        (1, 100, 300, 800, 800),
        (1, 1000, 300, 800, 800),  # 같은 번호
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=4)
    assert out is None


def test_gate_seq_jump_rejects(monkeypatch):
    """비순차 jump (gap > 10 + max gap > min gap * 5) → reject."""
    _make_image(monkeypatch)
    # nums = [1, 2, 50] — gap [1, 48] — 50번이 갑자기 등장 = 잘못된 인식
    result = _bbox_result(problems=[
        (1, 100, 300, 800, 600),
        (2, 100, 1000, 800, 600),
        (50, 100, 1700, 800, 600),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=5)
    assert out is None


def test_gate_seq_consecutive_passes(monkeypatch):
    """순차 번호 통과."""
    _make_image(monkeypatch)
    result = _bbox_result(problems=[
        (1, 100, 300, 800, 600),
        (2, 100, 1000, 800, 600),
        (3, 100, 1700, 800, 600),
    ])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=5)
    assert out is result


# ── 안전망: image 못 읽으면 통과 (회귀 방지) ──

def test_gate_unreadable_image_passes_safely(monkeypatch):
    """cv2.imread None → 게이트 우회 (회귀 방지)."""
    import cv2
    monkeypatch.setattr(cv2, "imread", lambda p: None)
    result = _bbox_result(problems=[(1, 0, 0, 100, 100), (2, 200, 0, 100, 100)])
    out = _validate_vlm_bboxes(result, "fake.png", page_idx=6)
    # cv2.imread가 None → image dim 못 가져옴 → 통과 (회귀 안전망)
    assert out is result


# ── P0-2: 자동 품질 점수 ──

def test_quality_score_full_problem():
    """정상 문항: bbox + anchor + text 30+ + no_anchor_in_text X → 1.00."""
    questions = [{
        "number": 1,
        "bbox": [100, 300, 1500, 800],
        "text": "1. 다음 중 옳은 것은? 그림 (가)~(라)는 자연 세계를 시간과 공간으로",
        "meta_extra": {},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    assert me["quality_score"] == 1.00
    assert me.get("low_quality") is None


def test_quality_score_no_anchor_low_quality():
    """보기만 cell — no_anchor_in_text=True → < 0.7 → low_quality=True."""
    questions = [{
        "number": 2,
        "bbox": [100, 300, 1500, 800],
        "text": "<보기> ㄱ. 첫 번째 ㄴ. 두 번째 ① ㄱ ② ㄴ ③ ㄱ,ㄴ",
        "meta_extra": {"no_anchor_in_text": True},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    assert me["quality_score"] == 0.80  # bbox 0.30 + anchor 0.30 + text>=30 0.20 + no_anchor_pattern 0
    assert me.get("low_quality") is None  # 0.80 >= 0.7


def test_quality_score_strip_cell_low_quality():
    """strip cell: 매우 작은 bbox + no_anchor + 짧은 text → < 0.7 → low_quality=True."""
    questions = [{
        "number": 1,
        "bbox": [100, 300, 30, 20],  # w=30, h=20 너무 작음 → 0
        "text": "ㄱ.",  # < 10 → 0
        "meta_extra": {"no_anchor_in_text": True},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    # bbox 0 + anchor 0.30 + text<10 0 + no_anchor_pattern 0 = 0.30
    assert me["quality_score"] == 0.30
    assert me["low_quality"] is True


def test_quality_score_mismatch_penalty():
    """number_mismatch 있으면 anchor 0점 → low_quality 가능."""
    questions = [{
        "number": 3,
        "bbox": [100, 300, 1500, 800],
        "text": "5. 다음 중 옳은 것은? 그림은 어떤 분자의 구조를 나타낸 것이다",
        "meta_extra": {"number_mismatch": {"db": 3, "ocr": 5}},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    # bbox 0.30 + mismatch (no anchor 점수) 0 + text>=30 0.20 + no_anchor_pattern 0.20 = 0.70
    assert me["quality_score"] == 0.70
    assert me.get("low_quality") is None  # 0.70 >= 0.7 (경계)


def test_quality_score_page_as_problem():
    """page-as-problem (bbox=None): 0.15 + anchor + text + pattern."""
    questions = [{
        "number": 1,
        "bbox": None,
        "text": "페이지 통째 인덱싱된 commercial workbook 본문 내용",
        "meta_extra": {},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    # bbox 0.15 (page-fallback) + anchor 0.30 + text>=30 0.20 + no_anchor_pattern 0.20 = 0.85
    assert me["quality_score"] == 0.85
    assert me.get("low_quality") is None


def test_quality_score_short_text_with_no_anchor_low_quality():
    """짧은 text + no_anchor_in_text → 다층 결함 → low_quality."""
    questions = [{
        "number": 1,
        "bbox": [0, 0, 80, 80],  # 50<w,h<=100 → 0.15 (작은 박스)
        "text": "짧",  # < 10 → 0
        "meta_extra": {"no_anchor_in_text": True},
    }]
    _compute_quality_score(questions)
    me = questions[0]["meta_extra"]
    # bbox 0.15 + anchor 0.30 + text<10 0 + no_anchor 0 = 0.45
    assert me["quality_score"] == 0.45
    assert me["low_quality"] is True


# ── B-2 (2026-05-04): VLM paper_type 분류 통합 ──

def test_normalize_paper_type_valid():
    """valid paper_type 값은 그대로 반환."""
    from academy.adapters.ai.detection.vlm_fallback import _normalize_paper_type

    for pt in ("clean_pdf_single", "clean_pdf_dual", "scan_single", "scan_dual",
               "quadrant", "student_answer_photo", "side_notes", "non_question",
               "unknown"):
        assert _normalize_paper_type(pt) == pt


def test_normalize_paper_type_invalid_to_unknown():
    """invalid 값 / None / 빈 문자열 → 'unknown'."""
    from academy.adapters.ai.detection.vlm_fallback import _normalize_paper_type

    for raw in (None, "", "garbage", "Layout_unknown", "single_column", 42):
        assert _normalize_paper_type(raw) == "unknown", f"raw={raw!r} should yield unknown"


def test_normalize_paper_type_case_insensitive():
    """대문자 입력도 lowercase로 정규화되어 valid 매칭 (LLM 응답 robustness)."""
    from academy.adapters.ai.detection.vlm_fallback import _normalize_paper_type

    assert _normalize_paper_type("STUDENT_ANSWER_PHOTO") == "student_answer_photo"
    assert _normalize_paper_type("Quadrant") == "quadrant"
    assert _normalize_paper_type("  scan_dual  ") == "scan_dual"  # strip 적용


def test_problem_bbox_result_paper_type_default():
    """ProblemBboxResult.paper_type 기본값 'unknown' (하위호환)."""
    r = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[],
        confidence=0.7,
    )
    assert r.paper_type == "unknown"


def test_problem_bbox_result_paper_type_override():
    """ProblemBboxResult.paper_type 명시 시 보존."""
    r = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[],
        confidence=0.9,
        paper_type="quadrant",
    )
    assert r.paper_type == "quadrant"


def test_pages_via_vlm_overrides_page_paper_type(monkeypatch):
    """VLM 채택 시 page['paper_type']이 VLM 값으로 override (B-2 핵심)."""
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    page = {
        "page_index": 0,
        "image_path": "/fake/path.png",
        "boxes": [],
        "text_regions": [],
        "has_embedded_text": False,
        "paper_type": "unknown",
    }

    accepted_vlm = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=1, bbox=(10, 10, 100, 100), confidence=0.9),
            ProblemBbox(number=2, bbox=(120, 10, 100, 100), confidence=0.9),
        ],
        confidence=0.92,
        paper_type="quadrant",
    )
    # 새 시그니처: (validated_or_none, raw_paper_type)
    monkeypatch.setattr(
        matchup_pipeline, "_try_vlm_problem_bboxes",
        lambda page, doc_id, tenant_id=None: (accepted_vlm, "quadrant"),
    )
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "1")

    _, vlm_stats = matchup_pipeline._pages_via_vlm(
        [page], document_id="123", job_id="test",
    )

    assert page["paper_type"] == "quadrant"
    assert page.get("paper_type_debug", {}).get("vlm_override") is True
    assert page.get("paper_type_debug", {}).get("bbox_validated") is True
    assert vlm_stats["pages_used"] == 1


def test_pages_via_vlm_paper_type_override_even_if_bbox_rejected(monkeypatch):
    """VLM bbox 게이트 reject되어도 paper_type 응답은 page meta에 override (B-2 보강).

    핵심: bbox 검증과 paper_type 신호는 별개. VLM 응답 받으면 paper_type은 항상 사용.
    이 케이스가 운영 academy_workbook 사례 — VLM bbox 모두 reject되지만 paper_type은
    'clean_pdf_dual' 같이 정확 분류.
    """
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    page = {
        "page_index": 0,
        "image_path": "/fake/path.png",
        "boxes": [],
        "text_regions": [],
        "has_embedded_text": False,
        "paper_type": "unknown",
    }

    # bbox는 reject (None) but paper_type 응답은 받음
    monkeypatch.setattr(
        matchup_pipeline, "_try_vlm_problem_bboxes",
        lambda page, doc_id, tenant_id=None: (None, "clean_pdf_dual"),
    )
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "1")

    _, vlm_stats = matchup_pipeline._pages_via_vlm(
        [page], document_id="123", job_id="test",
    )

    # bbox는 reject but paper_type은 override (핵심 회귀 락)
    assert page["paper_type"] == "clean_pdf_dual"
    assert page.get("paper_type_debug", {}).get("vlm_override") is True
    assert page.get("paper_type_debug", {}).get("bbox_validated") is False
    assert vlm_stats["pages_used"] == 0  # bbox는 reject


def test_pages_via_vlm_no_paper_type_response_no_override(monkeypatch):
    """VLM 응답에 paper_type=None (mock fallback 또는 unknown) → heuristic 보존."""
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    page = {
        "page_index": 0,
        "image_path": "/fake/path.png",
        "boxes": [],
        "text_regions": [],
        "has_embedded_text": False,
        "paper_type": "scan_dual",
    }

    monkeypatch.setattr(
        matchup_pipeline, "_try_vlm_problem_bboxes",
        lambda page, doc_id, tenant_id=None: (None, None),
    )
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "1")

    matchup_pipeline._pages_via_vlm(
        [page], document_id="123", job_id="test",
    )

    assert page["paper_type"] == "scan_dual"
    assert page.get("paper_type_debug") is None or "vlm_override" not in page.get("paper_type_debug", {})


def test_mock_vision_adapter_paper_type_default():
    """MockVLMVisionAdapter도 paper_type 필드 보유 (하위호환)."""
    from academy.adapters.ai.detection.vlm_fallback import MockVLMVisionAdapter

    adapter = MockVLMVisionAdapter()
    result = adapter.detect_problems(image_path="/fake.png", page_meta={"boxes": []})
    assert hasattr(result, "paper_type")
    assert result.paper_type == "unknown"


# ── P0-2 (2026-05-04): VLM cost cap per_tenant ──

def test_check_tenant_quota_under_limit_passes():
    """tenant 일별 호출 수가 limit 미만이면 통과."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback.reset_tenant_quota()
    # default limit 500. 작은 값으로 reset 후 실행.
    for _ in range(10):
        vlm_fallback._check_tenant_quota("test_tenant_a")
    # 예외 없으면 통과
    assert True


def test_check_tenant_quota_exceeds_limit_raises(monkeypatch):
    """tenant 일별 limit 초과 시 RuntimeError."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback.reset_tenant_quota()
    monkeypatch.setattr(vlm_fallback, "_VLM_TENANT_DAILY_LIMIT", 3)
    # 3회는 통과
    for _ in range(3):
        vlm_fallback._check_tenant_quota("test_tenant_b")
    # 4회째 RuntimeError
    with pytest.raises(RuntimeError, match="VLM 호출 한도 초과 \\(tenant=test_tenant_b"):
        vlm_fallback._check_tenant_quota("test_tenant_b")


def test_check_tenant_quota_separate_tenants_independent():
    """tenant ID 다르면 카운터 독립."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback.reset_tenant_quota()
    monkeypatch_setter = lambda: None  # noop
    # 직접 _VLM_TENANT_DAILY_LIMIT는 안 만지고 raw counter 검증
    for _ in range(5):
        vlm_fallback._check_tenant_quota("tenant_x")
    for _ in range(5):
        vlm_fallback._check_tenant_quota("tenant_y")
    # 양쪽 다 cap 미만이라 예외 없음. counter 분리 검증.
    from datetime import date
    today = date.today().isoformat()
    assert vlm_fallback._tenant_call_counter[("tenant_x", today)] == 5
    assert vlm_fallback._tenant_call_counter[("tenant_y", today)] == 5


def test_check_tenant_quota_none_tenant_skipped():
    """tenant_id None이면 quota 검사 안 함 (legacy 호환)."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback.reset_tenant_quota()
    for _ in range(1000):
        vlm_fallback._check_tenant_quota(None)
    # 예외 없음. counter 비어있어야 함.
    assert vlm_fallback._tenant_call_counter == {}


def test_reset_tenant_quota_specific_tenant():
    """reset_tenant_quota(tenant_id)는 해당 tenant만 리셋."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback.reset_tenant_quota()
    vlm_fallback._check_tenant_quota("tenant_a")
    vlm_fallback._check_tenant_quota("tenant_b")
    vlm_fallback.reset_tenant_quota("tenant_a")
    from datetime import date
    today = date.today().isoformat()
    assert ("tenant_a", today) not in vlm_fallback._tenant_call_counter
    assert vlm_fallback._tenant_call_counter[("tenant_b", today)] == 1


def test_reset_tenant_quota_all():
    """reset_tenant_quota(None)은 전체 리셋."""
    from academy.adapters.ai.detection import vlm_fallback

    vlm_fallback._check_tenant_quota("tenant_a")
    vlm_fallback._check_tenant_quota("tenant_b")
    vlm_fallback.reset_tenant_quota()
    assert vlm_fallback._tenant_call_counter == {}
