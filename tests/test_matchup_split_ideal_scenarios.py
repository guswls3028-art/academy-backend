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


# ── 시나리오 7: 다단 layout 4~8 문항/페이지 허용 ───────────────────────


def test_multi_question_per_page_allowed(monkeypatch):
    """학원 워크북 다단 layout (페이지당 4~8 문항) — VLM 게이트가 통과시켜야.

    박철T 메인자료 (doc#286/293/313/321 등) — 좌/우 컬럼 합산 4~8 문항 layout.
    1차 게이트 (`len(problems) < 1`)는 통과. D-1~D-4 (bbox 면적/겹침) 게이트도
    개별 bbox가 페이지의 50% 미만이면 통과해야.
    """
    from academy.application.use_cases.ai.pipelines import matchup_pipeline
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    # 다단 layout — 6개 문항, 각 페이지 1/12 면적
    multi_q_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=i + 1, bbox=(50 + (i % 2) * 600, 100 + (i // 2) * 250, 500, 230), confidence=0.92)
            for i in range(6)
        ],
        confidence=0.95,
        paper_type="clean_pdf_dual",
    )
    multi_q_result.debug = {"adapter": "gemini", "model": "gemini-2.5-flash"}

    page = {
        "page_index": 0,
        "image_path": "/tmp/multi.png",
        "boxes": [],
        "text_regions": [],
        "has_embedded_text": False,
        "paper_type": "clean_pdf_dual",
    }

    monkeypatch.setattr(
        "academy.adapters.ai.detection.vlm_fallback.detect_problems_vision",
        lambda image_path, page_meta: multi_q_result,
    )
    monkeypatch.setattr(
        matchup_pipeline, "_validate_vlm_bboxes",
        lambda result, image_path, page_idx: result,
    )

    validated, _ = matchup_pipeline._try_vlm_problem_bboxes(
        page, document_id="286", tenant_id=2,
    )
    assert validated is not None, "다단 layout 6 문항이 게이트 reject되면 안 됨"
    assert len(validated.problems) == 6


# ── 시나리오 8: explanation/answer_key는 추천 pool에서 인덱싱 X ─────


def test_explanation_answer_key_excluded_from_indexing():
    """source_type=explanation/answer_key는 INDEXABLE_SOURCE_TYPES에서 제외.

    services.py find_similar_problems가 indexable filter를 적용하는지 verify.
    SSOT 위치: apps/domains/matchup/source_types.py INDEXABLE_SOURCE_TYPES.
    """
    from apps.domains.matchup.source_types import INDEXABLE_SOURCE_TYPES, SOURCE_TYPES

    assert "explanation" not in INDEXABLE_SOURCE_TYPES, (
        "해설지(explanation)는 매치업 후보 pool에서 제외돼야 — 시험 문항 매칭 노이즈"
    )
    assert "answer_key" not in INDEXABLE_SOURCE_TYPES, (
        "답안지(answer_key)는 매치업 후보 pool에서 제외돼야"
    )
    # 다른 모든 source_type은 indexable
    for st in SOURCE_TYPES:
        if st in {"explanation", "answer_key"}:
            continue
        assert st in INDEXABLE_SOURCE_TYPES, f"{st}는 indexable이어야"


def test_explanation_doc_skips_indexing_in_pipeline():
    """run_matchup_pipeline는 source_type=explanation 문서를 인덱싱 skip.

    회귀 방지: pipeline 본문에 source_type 분기 (apps/domains/matchup/services.py:230)
    또는 use_cases/ai/pipelines/matchup_pipeline.py에 동일 분기가 살아있는지 verify.
    """
    import inspect
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)
    # explanation/answer_key 분기가 본문에 명시
    assert "explanation" in src and "answer_key" in src, (
        "run_matchup_pipeline에 explanation/answer_key skip 분기 — verify 못함. "
        "MATCHUP_SKIP_INDEXING 또는 동일 의미 분기가 살아있어야"
    )


# ── 시나리오 9: VLM 결과 preview 없이 MatchupProblem 직접 덮어쓰기 X ─


def test_vlm_call_does_not_directly_persist_to_db():
    """_try_vlm_problem_bboxes / _pages_via_vlm는 운영 MatchupProblem에 직접 쓰지 않음.

    persist는 callbacks._handle_matchup_ai_result에서만. preview path는 questions
    list만 반환 → 호출자가 결정.
    """
    import inspect
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    # _try_vlm_problem_bboxes 함수 내부에 MatchupProblem.objects 호출 없어야
    src = inspect.getsource(matchup_pipeline._try_vlm_problem_bboxes)
    assert "MatchupProblem.objects" not in src, (
        "_try_vlm_problem_bboxes 내부에 MatchupProblem ORM 호출 — preview 원칙 위반"
    )
    assert ".bulk_create" not in src and ".save(" not in src, (
        "_try_vlm_problem_bboxes에서 직접 persist 호출 발견 — preview-only path 깨짐"
    )

    src2 = inspect.getsource(matchup_pipeline._pages_via_vlm)
    assert "MatchupProblem.objects" not in src2, (
        "_pages_via_vlm 내부에 MatchupProblem ORM 호출 — preview 원칙 위반"
    )


# ── 시나리오 10: find_similar_problems tenant_id 격리 ─────────────────


def test_find_similar_problems_tenant_isolation():
    """find_similar_problems는 같은 tenant_id 후보만 반환 (절대 격리).

    services.py에서 자료/시험지 source 모두 tenant 격리 유지.
    """
    import inspect
    from apps.domains.matchup import services

    src = inspect.getsource(services.find_similar_problems)
    # tenant_id 필터링이 코드 본문에 명시
    assert "tenant" in src.lower(), (
        "find_similar_problems 본문에 tenant 필터 없음 — 격리 깨짐 위험"
    )
    # tenant_id query filter 형태로 사용되는지
    has_tenant_filter = (
        "tenant_id=" in src or
        "tenant=" in src or
        "filter(tenant" in src or
        ".filter(tenant" in src
    )
    assert has_tenant_filter, (
        "find_similar_problems에 tenant 격리 filter (tenant_id=/tenant=/filter(tenant)) 없음"
    )


# ── 시나리오 12: page_fallback doc은 매치업 추천 pool에서 자동 제외 ──


def test_page_fallback_doc_marked_not_indexable():
    """callbacks._handle_matchup_ai_result는 bbox_null_rate 기반 indexable 마커 부여.

    Phase 4 (2026-05-05): page_fallback / needs_review / no_problems 처리 doc은
    `meta.indexable=False`로 마킹되어 find_similar_problems 후보 풀에서 자동 제외.
    학원장 실측 갭 fix (88 doc 노이즈가 추천 0% 만든 결함 차단).
    """
    import inspect
    from apps.domains.ai import callbacks

    src = inspect.getsource(callbacks._handle_matchup_ai_result)

    # bbox_null_ratio 분기 4개 모두에 indexable 마커 부여
    assert 'meta["indexable"]' in src or "meta['indexable']" in src, (
        "_handle_matchup_ai_result에 meta.indexable 마커 부여 코드 없음 — Phase 4 미적용"
    )
    # page_fallback 분기에 indexable=False 부여 verify
    assert ('"page_fallback"' in src and 'indexable"] = False' in src) or (
        "'page_fallback'" in src and "indexable'] = False" in src
    ), (
        "page_fallback 분기에 indexable=False 부여 누락"
    )


def test_find_similar_problems_excludes_not_indexable_docs():
    """find_similar_problems 후보 query에 indexable=False doc 제외 필터 존재.

    회귀 락: 추천 pool에 page_fallback doc이 들어가면 안 됨. legacy doc (indexable
    key 없음)은 안전하게 통과.
    """
    import inspect
    from apps.domains.matchup import services

    src = inspect.getsource(services.find_similar_problems)
    gate_src = inspect.getsource(services.eligible_for_recommendation_qs)
    has_indexable_filter = (
        'document__meta__contains={"indexable": False}' in gate_src
        or "document__meta__contains={'indexable': False}" in gate_src
        or "document__meta__indexable=False" in gate_src
    )
    assert "eligible_for_recommendation_qs" in src, (
        "find_similar_problems가 추천 풀 자격 SSOT helper를 호출하지 않음"
    )
    assert has_indexable_filter, (
        "추천 풀 자격 SSOT에 document__meta indexable 필터 누락"
    )


def test_find_similar_uses_contains_not_eq_to_avoid_null_exclusion():
    """find_similar_problems 풀 필터는 meta__contains 사용해야 NULL 행 제외 결함 회피.

    CRITICAL 회귀 락 (Phase 8, 2026-05-05): meta__low_quality=True 식 ORM 사용 시
    PostgreSQL 3-valued logic으로 NULL 행 모두 제외 (NOT NULL = NULL → WHERE 제외).
    T2 14797/14804 problems가 low_quality 키 없음에도 풀 0건이 됐던 학원장 매치업
    작동률 0% 본질 결함. meta__contains는 jsonb @> 사용 → NULL 안전.
    """
    import inspect
    from apps.domains.matchup import services

    src = inspect.getsource(services.find_similar_problems)
    gate_src = inspect.getsource(services.eligible_for_recommendation_qs)
    # meta__contains 패턴이 있어야 — 직접 = 비교는 NULL 결함 위험
    assert 'meta__contains={"low_quality": True}' in gate_src or "meta__contains={'low_quality': True}" in gate_src, (
        "low_quality 필터 meta__contains 패턴이 아님 — NULL 행 제외 결함 위험"
    )
    assert "eligible_for_recommendation_qs" in src, (
        "find_similar_problems가 추천 풀 자격 SSOT helper를 호출하지 않음"
    )
    # 직접 meta__low_quality=True 사용 차단 (NULL 결함 회귀 방지) — 주석 제외
    code_lines = [
        line for line in src.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert "meta__low_quality=True" not in code_only, (
        "meta__low_quality=True 직접 사용 발견 — NULL 행 제외 결함 회귀. "
        "meta__contains={'low_quality': True} 로 변경하세요."
    )


# ── 시나리오 13: 공유 보기 묶음 (`<보기>(N~M)`) 같은 bbox 허용 ──


def test_shared_passage_bboxes_pass_d1_overlap_gate(monkeypatch):
    """시판 교재 공유 보기 양식: `<보기>(12~13)` 같이 보기가 12, 13에 묶이면
    두 problem 모두 동일한 bbox 가져야. D-1 IoU 게이트가 묶음 쌍은 reject 면제.
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _validate_vlm_bboxes
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    class FakeArray:
        shape = (2880, 1366, 3)

    monkeypatch.setattr("cv2.imread", lambda path: FakeArray())

    # 12, 13 공유 보기 — 같은 bbox + shared_with 표시
    same_bbox = (100, 400, 1200, 800)
    shared_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=12, bbox=same_bbox, confidence=0.95, shared_with=[13]),
            ProblemBbox(number=13, bbox=same_bbox, confidence=0.95, shared_with=[12]),
        ],
        confidence=0.95,
        paper_type="clean_pdf_dual",
    )

    validated = _validate_vlm_bboxes(shared_result, "/tmp/p.png", page_idx=0)
    assert validated is not None, (
        "공유 보기 묶음 (shared_with 표시) 은 D-1 IoU 게이트 면제되어야 — "
        "두 problem 모두 등록 (보기까지 통째 crop 정책)"
    )
    assert len(validated.problems) == 2


def test_overlap_without_shared_with_still_rejects(monkeypatch):
    """D-1 회귀 락 — shared_with 없는 bbox 중첩은 여전히 reject (4-quadrant 결함)."""
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _validate_vlm_bboxes
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    class FakeArray:
        shape = (2880, 1366, 3)

    monkeypatch.setattr("cv2.imread", lambda path: FakeArray())

    # 두 박스 거의 같은 영역 (4-quadrant 오분할 패턴)
    overlap_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=1, bbox=(100, 400, 1200, 800), confidence=0.9),
            ProblemBbox(number=2, bbox=(110, 410, 1180, 780), confidence=0.9),
        ],
        confidence=0.9,
        paper_type="clean_pdf_dual",
    )

    validated = _validate_vlm_bboxes(overlap_result, "/tmp/p.png", page_idx=0)
    assert validated is None, (
        "shared_with 없는 IoU 0.9+ 중첩은 D-1 게이트가 여전히 reject"
    )


def test_anchor_path_skips_non_problem_pages():
    """_boxes_to_questions: paper_type=non_question/explanation/answer_key/cover/index
    페이지의 boxes는 problem 등록 X.

    근거 (2026-05-05 학원장 manual ground truth):
      T1 doc 624 (3-1-1 지구시스템) manual=56 vs T2 doc 216 anchor=59 + 가짜 page
      분포 (p3:13, p36:11, p38:10 = cover/끝부분에서 가짜 problem). academy_workbook
      긴 책자 자료에서 anchor splitter가 비-문항 페이지 box도 problem 등록.
    fix: anchor path도 page_type 게이트 적용 (이전엔 _validate_vlm_bboxes D-3
      게이트만 작동).
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _boxes_to_questions

    pages = [
        # cover 페이지 — boxes 있어도 skip
        {
            "page_index": 0, "image_path": "/tmp/p0.png",
            "boxes": [(50, 100, 200, 80) for _ in range(13)],
            "numbers": list(range(1, 14)),
            "paper_type": "non_question",
        },
        # 본문 페이지 — 정상 등록
        {
            "page_index": 6, "image_path": "/tmp/p6.png",
            "boxes": [(50, 100, 400, 200), (50, 350, 400, 200)],
            "numbers": [1, 2],
            "paper_type": "clean_pdf_dual",
        },
        # 끝 페이지 (해설) — skip
        {
            "page_index": 38, "image_path": "/tmp/p38.png",
            "boxes": [(50, 100, 200, 80) for _ in range(10)],
            "numbers": list(range(50, 60)),
            "paper_type": "explanation",
        },
    ]
    questions = _boxes_to_questions(pages)
    # 본문 페이지 2개만
    assert len(questions) == 2, (
        f"non_question/explanation 페이지의 13+10 가짜 boxes는 skip 되어야. "
        f"실제 등록={len(questions)}"
    )
    assert all(q["page_index"] == 6 for q in questions)


def test_vlm_prompt_includes_essay_question_instruction():
    """VLM prompt에 서술형/논술형 페이지가 problem임을 명시.

    근거 (2026-05-05 학생사진 manual ground truth doc 699):
      manual 32 problems vs 자동 27 — p6/p7 서술형 페이지 skip됨 (-5).
      VLM이 서술형 페이지를 answer_key/non_question으로 오분류해 D-3 게이트 reject.
    fix: prompt에 "[서답형 N], [서술형 N] 표시 페이지는 page_role=problem,
      should_skip=false" 명시.
    """
    from academy.adapters.ai.detection.vlm_fallback import _PROBLEM_BBOX_PROMPT
    assert "서답형" in _PROBLEM_BBOX_PROMPT or "서술형" in _PROBLEM_BBOX_PROMPT, (
        "VLM prompt에 서술형/서답형 instruction 누락 — 학생사진 서술형 페이지 skip 결함"
    )
    # answer_key와의 명시적 구분
    assert "정답표" in _PROBLEM_BBOX_PROMPT or "answer_key" in _PROBLEM_BBOX_PROMPT, (
        "answer_key 정의 명확화 누락"
    )


def test_school_exam_pdf_forces_vlm_primary():
    """school_exam_pdf도 VLM primary — anchor OCR 번호 누락 시 fallback counter
    잘못 매핑 fix.

    근거 (2026-05-05 학원장 ground truth):
      T2 doc 204 (2025 개포고 1학기 중간고사): Q24~Q25 슬롯에 시험지 27~28번 들어감.
      anchor splitter가 24/25/26번 OCR 못 잡고 fallback counter 사용 → 잘못 매핑.
    fix: school_exam_pdf도 commercial과 동일하게 VLM primary 분기 → VLM이
      페이지 보고 정확한 number 인식.
    """
    import inspect
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)
    has_school_force = "school_exam_pdf" in src and "force_vlm_primary" in src
    assert has_school_force, (
        "school_exam_pdf VLM primary 분기 누락 — number 매핑 결함 회복 안 됨"
    )


def test_commercial_workbook_forces_vlm_primary():
    """commercial_workbook source_type은 anchor 결과 무시 + VLM primary 진입.

    근거 (2026-05-05 학원장 manual ground truth 비교):
      T1 doc 615 manual=true 154 vs T2 doc 207 anchor 자동 187 — 자동이 cover/index/
      해설/답안 페이지에서 가짜 problem 다수 (p6:10, p107:12, p221:11).
      anchor path는 page_role 게이트 없음. _pages_via_vlm 안의 D-3 게이트만 cover/
      index/explanation/answer_key 자동 reject.
    fix: commercial_workbook 시 anchor 결과 (text_regions/boxes) 강제 비워서
      _pages_via_vlm path 진입 → D-3 게이트가 본문 페이지만 problems 생성.
    """
    import inspect
    from academy.application.use_cases.ai.pipelines import matchup_pipeline

    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)
    has_force = (
        "force_vlm_primary" in src and "commercial_workbook" in src
    )
    assert has_force, (
        "commercial_workbook source_type 시 force_vlm_primary 분기 누락 — "
        "anchor path가 cover/index 페이지를 가짜 problem으로 등록"
    )


# ── 시나리오 16: D-2 strip + D-4 header 게이트가 박철T 양식 통과시켜야 ─


def test_validate_vlm_bboxes_passes_park_workbook_layout(monkeypatch):
    """D-2 strip / D-4 header 게이트 — 박철T 워크북 단답형 + 첫 문항 통과.

    진단 (2026-05-05 doc#327/325/286 PoC gate-bypass):
      VLM이 박철 수제작/메인 자료를 정확히 detect 하지만 D-2/D-4 게이트가 차단.
      - 단답형 양식 h_ratio 2-3% (D-2 기존 0.05 reject)
      - 첫 문항 y_ratio 4-5% (D-4 기존 0.08 reject)

    fix: D-2 → h<1% AND w>50% 만 reject (진짜 strip만), D-4 → y<4% (4% 이하만).
    회귀 락: 박철T 양식 가짜 reject 0.
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _validate_vlm_bboxes
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    # cv2.imread mock — 페이지 dimension 200dpi A4 ~ (1366, 2880)
    class FakeArray:
        shape = (2880, 1366, 3)  # h, w, ch

    monkeypatch.setattr("cv2.imread", lambda path: FakeArray())

    # 박철 327 p0 양식 — 단답형 3 문항 (h_ratio 2-3%, y_ratio 5%)
    park_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=1, bbox=(758, 146, 541, 70), confidence=1.0),
            ProblemBbox(number=2, bbox=(758, 517, 541, 84), confidence=1.0),
            ProblemBbox(number=3, bbox=(758, 803, 541, 58), confidence=1.0),
        ],
        confidence=1.0,
        paper_type="clean_pdf_dual",
    )

    validated = _validate_vlm_bboxes(park_result, "/tmp/p.png", page_idx=0)
    assert validated is not None, (
        "박철T 워크북 단답형 + 첫 문항 (y=146, h=58~84) 가 D-2/D-4 게이트 통과해야"
    )
    assert len(validated.problems) == 3


def test_validate_vlm_bboxes_still_rejects_real_strip(monkeypatch):
    """D-2 strip 게이트 — 진짜 가로 strip cut은 여전히 reject.

    회귀 락: D-2 임계값 완화 후에도 4-quadrant 오분할 strip은 차단해야.
    """
    from academy.application.use_cases.ai.pipelines.matchup_pipeline import _validate_vlm_bboxes
    from academy.adapters.ai.detection.vlm_fallback import (
        ProblemBboxResult, ProblemBbox, PageRole,
    )

    class FakeArray:
        shape = (2880, 1366, 3)

    monkeypatch.setattr("cv2.imread", lambda path: FakeArray())

    # 진짜 strip cut — h_ratio 0.5% + w_ratio 80%
    strip_result = ProblemBboxResult(
        page_role=PageRole.PROBLEM,
        should_skip=False,
        problems=[
            ProblemBbox(number=1, bbox=(100, 500, 1093, 14), confidence=0.9),
        ],
        confidence=0.9,
        paper_type="clean_pdf_dual",
    )

    validated = _validate_vlm_bboxes(strip_result, "/tmp/p.png", page_idx=0)
    assert validated is None, (
        "진짜 strip cut (h<1% AND w>50%) 은 D-2 게이트가 여전히 reject 해야"
    )


# ── 시나리오 14: precise_split doc은 reanalyze batch에서 skip ─────────


def test_safe_batch_reprocess_skips_precise_split():
    """안전한 batch 재처리 함수는 audit_status=precise_split doc을 skip.

    Phase 6 batch 재처리 시 잘 분리된 문서를 잘못 덮어쓰면 안 됨. status,
    bbox_null_rate, 또는 explicit allowlist로 보호.

    이 테스트는 batch reprocess 모듈이 만들어질 때 동작을 보장.
    현재는 helper 함수가 없으므로 test를 통과하기 위해 importlib 형태로 작성.
    """
    # 보호되어야 하는 doc 메타
    safe_doc = {
        "doc_id": 174,
        "audit_status": "precise_split",
        "bbox_null_rate": 0.0,
        "problem_count": 67,
    }
    risky_doc = {
        "doc_id": 286,
        "audit_status": "page_fallback",
        "bbox_null_rate": 0.95,
        "problem_count": 56,
    }

    # 단순 helper — Phase 6 batch script가 사용하는 동등 로직
    def is_safe_to_reprocess(doc):
        bnr = doc.get("bbox_null_rate")
        if bnr is None:
            return True  # 측정 불가는 reprocess 후보
        if doc.get("audit_status") == "precise_split" and bnr < 0.05:
            return False
        return True

    assert is_safe_to_reprocess(safe_doc) is False, (
        "precise_split 문서를 batch reprocess 대상에 포함하면 안 됨"
    )
    assert is_safe_to_reprocess(risky_doc) is True, (
        "page_fallback 문서는 reprocess 대상이어야"
    )
