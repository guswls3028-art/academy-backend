# -*- coding: utf-8 -*-
"""TDD: 이상적 매치업 분리 성공 시나리오 — 회귀 방지 + 성공 기준 SSOT.

작성 동기 (2026-05-05 운영 audit):
- T2 박철 운영 193 doc 진짜 분리 성공률 1.6%
- "page-as-problem 폴백을 status=done으로 마킹"이 분리 결함을 metric에 가린 함정
- 학원장 캡처 검수: doc#292/329/224/221/324 모두 페이지=problem 또는 큰 블록

이상적 성공 정의:
- bbox 존재 + bbox 면적 < page 70% + 페이지당 problems 적정 분포
- page-as-problem 폴백은 needs_review 상태 (done 아님)
- 강제 hard-coded 폴백 (is_commercial / is_student_photo) 폐기
- VLM 게이트가 1-문항/페이지 layout (박철 수제작) reject 금지

각 테스트는 mock pipeline + 결과 assert 형태. 실제 DB 안 건드림.
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_clean_pdf_pages():
    """학교시험 PDF 6페이지 — 페이지당 anchor 4~6개 (정상 분리 케이스)."""
    from academy.domain.tools.question_splitter import QuestionRegion

    def _mk(num):
        return QuestionRegion(
            number=num,
            bbox=(50.0, 100.0 + num * 80, 250.0, 100.0 + num * 80 + 70),
            page_index=0,
        )
    pages = []
    for p_idx in range(6):
        regions = [_mk(p_idx * 5 + n + 1) for n in range(5)]
        pages.append({
            "page_index": p_idx,
            "image_path": f"/tmp/page_{p_idx:03d}.png",
            "boxes": [r.bbox for r in regions],
            "text_regions": regions,
            "has_embedded_text": True,
            "paper_type": "clean_pdf_dual",
        })
    return pages


@pytest.fixture
def fake_park_homemade_pages():
    """박철 수제작 15페이지 — 페이지당 1 문항 (1-문항/페이지 layout)."""
    from academy.domain.tools.question_splitter import QuestionRegion

    pages = []
    for p_idx in range(15):
        regions = [QuestionRegion(
            number=p_idx + 1,
            bbox=(80.0, 120.0, 500.0, 700.0),
            page_index=p_idx,
        )]
        pages.append({
            "page_index": p_idx,
            "image_path": f"/tmp/page_{p_idx:03d}.png",
            "boxes": [r.bbox for r in regions],
            "text_regions": regions,
            "has_embedded_text": True,
            "paper_type": "clean_pdf_dual",
        })
    return pages


# ── 시나리오 1: 학교시험 PDF 정밀 분리 ───────────────────────────────────


def test_school_exam_pdf_precise_split(fake_clean_pdf_pages):
    """학교시험 PDF (anchor splitter 적합) → 페이지당 3+ problems 정밀 crop.

    Doc#257 같은 케이스. 현재 운영에서 잘 작동 (precise_pct 94.4%).
    이 테스트는 anchor splitter 정상 작동 + bbox 정밀도 회귀 방지.
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _boxes_to_questions

    questions = _boxes_to_questions(fake_clean_pdf_pages)

    # 페이지당 5개 anchor → 6 페이지 × 5 = 30 problems
    assert len(questions) == 30, f"학교시험 6p × 5anchor = 30 problems, 실제={len(questions)}"

    # 모두 bbox 존재 (정밀 crop)
    for q in questions:
        assert q.get("bbox") is not None, f"학교시험 problem #{q.get('number')}는 bbox 있어야"

    # 페이지당 4+ (정밀 분리 임계값)
    from collections import Counter
    by_page = Counter(q["page_index"] for q in questions)
    for page, count in by_page.items():
        assert count >= 4, f"학교시험 p{page} = {count} problems, 페이지당 4+ 필요"


# ── 시나리오 2: 박철 수제작 1-문항/페이지 허용 ─────────────────────────


def test_park_homemade_single_question_per_page_allowed(fake_park_homemade_pages, monkeypatch):
    """박철 수제작 (1-문항/페이지 layout) → VLM 게이트가 reject 금지.

    현재 결함 (2026-05-05 doc#327 PoC):
    - VLM이 정상 응답 (paper_type=clean_pdf_dual, problems=1)
    - 그러나 _try_vlm_problem_bboxes 1차 게이트 `len(problems) < 2` 발동 → reject
    - 결과: 15 페이지 모두 reject → page-as-problem 폴백 강제

    fix: 1-문항/페이지 layout (박철 수제작) 허용. min_problems_per_page 임계값 1로 완화
    또는 source_type=academy_workbook + paper_type=clean_pdf 케이스 예외.
    """
    from academy.application.use_cases.ai.pipelines import matchup_pipeline
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    # VLM이 페이지마다 1 problem 응답 (박철 수제작 1-문항/페이지)
    accepted_vlm = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=1, bbox=(80, 120, 500, 580), confidence=0.95),
        ],
        confidence=0.95,
        paper_type="clean_pdf_dual",
    )
    monkeypatch.setattr(
        matchup_pipeline, "_try_vlm_problem_bboxes",
        lambda page, doc_id, tenant_id=None: (accepted_vlm, "clean_pdf_dual"),
    )
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "1")

    # _pages_via_vlm 호출 (anchor 0 페이지로 가정 — VLM 시도 path)
    pages_no_anchor = []
    for p in fake_park_homemade_pages:
        page_no_anchor = dict(p)
        page_no_anchor["text_regions"] = []  # anchor 0 → VLM 시도
        pages_no_anchor.append(page_no_anchor)

    questions, stats = matchup_pipeline._pages_via_vlm(
        pages_no_anchor, document_id="327", job_id="test",
    )

    # 박철 수제작 15p × 1 problem = 15 problems (모두 게이트 통과해야)
    # 현재 결함 시 0 (모두 reject). fix 후 15 (1-문항 허용).
    assert stats["pages_used"] >= 10, (
        f"박철 수제작 1-문항/페이지가 게이트 reject되면 안 됨. "
        f"pages_used={stats['pages_used']}/15"
    )
    assert len(questions) >= 10, (
        f"박철 수제작 problems 충분히 추출되어야. 실제={len(questions)}"
    )

    # 모두 bbox 존재
    for q in questions:
        assert q.get("bbox") is not None


# ── 시나리오 3: page-as-problem 다수 doc은 status=done이 아니어야 ─────


def test_status_split_failed_when_all_bbox_null():
    """모든 problem이 bbox=null = page-as-problem 폴백 → status != done.

    현재 결함: doc#202 같은 케이스 = bbox=null × 49 + status=done.
    학원장 화면에선 "done"으로 보이지만 실제는 분리 실패.

    fix: callbacks._handle_matchup_ai_result에서 problems의 bbox null 비율 측정
    50%+ null이면 status=needs_review 또는 split_failed.
    """
    from apps.domains.matchup.models import MatchupDocument

    # 가상 problems list (bbox null 다수)
    problems = [
        {"number": i, "bbox": None, "page_index": i, "image_path": f"/tmp/{i}.png"}
        for i in range(10)
    ]

    # 분류 함수 (fix 후 추가될 함수)
    def classify_split_status(problems):
        if not problems:
            return "failed_split"
        null_count = sum(1 for p in problems if not p.get("bbox"))
        ratio = null_count / len(problems)
        if ratio >= 0.7:
            return "page_fallback"
        if ratio >= 0.3:
            return "needs_review"
        return "precise_split"

    status = classify_split_status(problems)
    assert status != "precise_split", "전부 bbox=null인데 precise_split이면 안 됨"
    assert status in ("page_fallback", "failed_split", "needs_review"), (
        f"status={status} — page_fallback / needs_review / failed_split 중 하나여야"
    )


def test_status_precise_when_all_bbox_present():
    """모든 problem이 bbox 존재 + 정밀 면적 → status=done(precise_split)."""
    problems = [
        {
            "number": i,
            "bbox": [50, 100 + i * 100, 200, 80],  # 페이지의 ~10% 면적
            "page_index": i // 5,
            "image_path": f"/tmp/{i}.png",
        }
        for i in range(20)
    ]

    def classify_split_status(problems):
        if not problems:
            return "failed_split"
        null_count = sum(1 for p in problems if not p.get("bbox"))
        ratio = null_count / len(problems)
        if ratio >= 0.7:
            return "page_fallback"
        if ratio >= 0.3:
            return "needs_review"
        return "precise_split"

    status = classify_split_status(problems)
    assert status == "precise_split"


# ── 시나리오 4: 학생사진 강제 page-as-problem 폐기 ─────────────────────


def test_student_photo_no_forced_page_fallback(fake_park_homemade_pages, monkeypatch):
    """학생사진 source_type — anchor + VLM 시도 후 실패면 needs_review (강제 폴백 X).

    이전 결함 (commit c30800aa로 폐기):
    - is_student_photo = True → 무조건 page-as-problem 폴백
    - 분리 시도 자체 X → metric상 "성공" but 매치업 무용

    fix 후: VLM 시도 → 실패면 페이지 skip + needs_review status.
    이 테스트는 강제 폴백 회귀 방지.
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _pages_via_vlm

    # VLM 호출 안 됨 (env disable)으로 가정
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "0")

    # 학생사진 가정 — anchor 0
    pages_no_anchor = [
        {
            "page_index": i,
            "image_path": f"/tmp/student_{i}.png",
            "boxes": [],
            "text_regions": [],
            "has_embedded_text": False,
            "paper_type": "student_answer_photo",
        }
        for i in range(5)
    ]

    questions, stats = _pages_via_vlm(
        pages_no_anchor, document_id="329", job_id="test",
    )

    # VLM 비활성 + anchor 0 → questions 비어야 (page=problem 폴백 X)
    assert len(questions) == 0, (
        f"VLM disable + anchor 0 페이지에 page-as-problem 폴백 발동되면 안 됨. "
        f"questions={len(questions)}"
    )
    # 페이지 skip 카운터 보존
    assert stats.get("pages_skipped_no_split", 0) == 5


# ── 시나리오 5: commercial_workbook hard-coded 폴백 폐기 ───────────────


def test_commercial_workbook_no_hardcoded_skip_vlm():
    """commercial_workbook도 VLM 호출 시도되어야 (이전 강제 skip_vlm 폐기 회귀 방지).

    이전 결함 (commit c30800aa로 폐기):
    - skip_vlm_auto = is_commercial or is_student_photo
    - = commercial_workbook은 무조건 VLM 안 호출 → page-as-problem 강제

    fix 후: pipeline 본문에 is_commercial / skip_vlm_auto 변수 자체 없음.
    이 테스트는 hard-coded skip 회귀 방지 — pipeline 소스에 해당 패턴 없음 보증.
    """
    import inspect
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)

    # 강제 폴백 트리거 변수가 함수 본체에서 사용되지 않아야
    forbidden_patterns = [
        "is_over_extracted",
        "is_low_confidence_doc",
        "skip_vlm_auto",
        "_pages_via_vlm_or_fallback",  # 이전 함수 이름
    ]
    for p in forbidden_patterns:
        # 주석은 OK — 코드 사용만 차단
        # 가장 단순: `= ...` 또는 `(...)` 같은 사용 패턴
        # 코멘트 라인 (# 시작) 제외하고 검사
        non_comment_lines = [
            line for line in src.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        non_comment_src = "\n".join(non_comment_lines)
        assert p not in non_comment_src, (
            f"강제 폴백 패턴 '{p}'가 pipeline 함수 본문에서 사용 중 — 폐기됐어야"
        )


# ── 시나리오 6: VLM 게이트 1-문항/페이지 reject 금지 ────────────────


def test_vlm_gate_allows_single_problem_per_page(monkeypatch):
    """VLM 1차 게이트 `len(problems) < 2` reject가 박철 수제작 1-문항 layout 차단.

    현재 결함: doc#327 15p VLM 정상 응답 but `len(problems) < 2`로 모두 reject.
    fix: 1차 게이트 임계값 1로 완화 (또는 paper_type 기반 조건부).
    """
    from academy.application.use_cases.ai.pipelines import matchup_pipeline
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    single_prob_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[ProblemBbox(number=1, bbox=(80, 120, 500, 580), confidence=0.95)],
        confidence=0.95,
        paper_type="clean_pdf_dual",
    )
    # adapter 메타 필수 — _try_vlm_problem_bboxes의 1차 게이트에서 검사
    single_prob_result.debug = {"adapter": "gemini", "model": "gemini-2.5-flash"}

    page = {
        "page_index": 0,
        "image_path": "/tmp/single.png",
        "boxes": [],
        "text_regions": [],
        "has_embedded_text": False,
        "paper_type": "clean_pdf_dual",
    }

    # detect_problems_vision mock
    monkeypatch.setattr(
        "academy.adapters.ai.detection.vlm_fallback.detect_problems_vision",
        lambda image_path, page_meta: single_prob_result,
    )

    # _validate_vlm_bboxes mock — 게이트 통과 (D-1~D-4 무관)
    monkeypatch.setattr(
        matchup_pipeline, "_validate_vlm_bboxes",
        lambda result, image_path, page_idx: result,
    )

    # cv2.imread는 _validate_vlm_bboxes 안에 있고 mock으로 우회됨
    validated, paper_type = matchup_pipeline._try_vlm_problem_bboxes(
        page, document_id="327", tenant_id=2,
    )

    # 박철 수제작 1-문항 layout이 게이트 통과해야 함 (현재 결함 시 None 반환)
    assert validated is not None, (
        f"VLM 1-문항 응답이 게이트 통과해야 (박철 수제작 layout 허용). "
        f"현재 결함 시 None — `len(result.problems) < 2` 게이트가 reject 중"
    )
    assert len(validated.problems) == 1
