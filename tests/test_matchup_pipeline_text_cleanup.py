"""
Matchup pipeline 텍스트 정제 회귀 테스트 — Tenant 2 (tchul) 운영 결함.

진단 (2026-04-28 운영 DB 직접 조사):
- 13개 학습자료 doc에서 problem.text에 "신민 TWORKBOOK" / "Runner S high with God min"
  / "Step N. 개념완성" 등 페이지 워터마크/단원헤더가 prepend됨 (~437건)
- doc#131 q4 = "13. 표는... 15. 그림은..." 두 문항이 한 box로 합쳐짐 (box-merge)
- title 키워드만으로 intent 자동 추정해 페이지 폴백 정합성 확보

각 케이스는 운영 problem.text 실측 데이터에서 추출.
"""
from __future__ import annotations


def test_strip_watermark_shinmin_tworkbook():
    """`신민 TWORKBOOK` 워터마크 제거 (운영 doc#123/144/126/145, 50건/문서)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    src = "신민 TWORKBOOK 1. 다음은 측정과 관련된 설명이다."
    out = strip_page_noise(src)
    assert "TWORKBOOK" not in out
    assert "1. 다음은 측정과 관련된 설명이다" in out


def test_strip_watermark_runners_high_god_min():
    """`Runner S high with God min` 푸터 제거 (운영 doc#120 q10/14/16/18/20)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    src = "Runner S high with God min 5. 다음은 측정과 관련된 설명이다"
    out = strip_page_noise(src)
    assert "Runner" not in out
    assert "God" not in out
    assert "5. 다음은 측정과 관련된 설명이다" in out


def test_strip_watermark_runners_high_apostrophe_variants():
    """OCR이 어포스트로피를 흘리는 변형도 잡아야 함."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    for variant in [
        "Runner's high with God min",
        "RUNNER'S HIGH WITH GOD MIN",
        "Runners high with God Min",
    ]:
        out = strip_page_noise(f"{variant} 본문 텍스트")
        assert "본문 텍스트" in out
        assert "Runner" not in out


def test_strip_unit_step_headers():
    """`Step 1. 개념완성` / `Step 2. 내신완성` / `Step 3. 수능완성` 단원 헤더 제거 (doc#120 11건)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    src = "Step 1. 개념완성 1. 시간 과 공간 의 기술 에 대한 설명"
    out = strip_page_noise(src)
    assert "개념완성" not in out
    assert "1. 시간 과 공간" in out


def test_strip_chapter_header_line():
    """`6 CHAPTER 01 과학 의 기초` 챕터 헤더 라인 제거."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    src = "6 CHAPTER 01 과학 의 기초\n추가 설명 ⊕ 수소 원자 - 수소 원자 지름 : 약 0.1 nm"
    out = strip_page_noise(src)
    assert "CHAPTER" not in out
    assert "수소 원자" in out


def test_strip_lorem_ipsum_residue():
    """라틴 lorem ipsum 잔재 제거 (doc#143 표지 spillover)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    src = "is dolore te feugait nulla consectetuer 1. 표는 주기율표를 나타낸 것이다"
    out = strip_page_noise(src)
    assert "consectetuer" not in out
    assert "1. 표는 주기율표" in out


def test_strip_preserves_content_intact():
    """본문 의미를 손상시키지 않음 (false positive 회귀 방지)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    # '신민' 단독, 'Step' 단독은 본문에 등장 가능 — 워터마크 풀 패턴만 제거.
    src = "1. 다음 글에서 신민이 등장하는 인물의 특징을 찾으시오."
    out = strip_page_noise(src)
    assert "신민이 등장" in out


def test_strip_empty_input():
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import strip_page_noise
    assert strip_page_noise("") == ""
    assert strip_page_noise(None) == ""  # type: ignore[arg-type]


# ── _flag_merge_suspect ──

def test_flag_merge_suspect_dual_anchor_in_long_text():
    """text 길이 800+ AND 본문에 추가 anchor → merge_suspect=True (doc#131 q4 패턴)."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import _flag_merge_suspect
    long_body = "표는 별 내부에서 일어나는 핵융합 반응을 나타낸 것이다. " * 30
    questions = [{
        "number": 13,
        "text": f"13. {long_body}\n15. 그림은 어느 별의 진화 과정을 나타낸 것이다.",
        "page_index": 5,
    }]
    _flag_merge_suspect(questions)
    assert questions[0]["meta_extra"]["merge_suspect"] is True
    assert questions[0]["meta_extra"]["merge_inner_anchors"] >= 1


def test_flag_merge_suspect_short_text_skipped():
    """짧은 본문(< 800)은 false positive 위험으로 검사 제외."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import _flag_merge_suspect
    questions = [{"number": 1, "text": "1. 짧은 문제\n3. 다른 인용", "page_index": 0}]
    _flag_merge_suspect(questions)
    assert "merge_suspect" not in questions[0].get("meta_extra", {})


def test_flag_merge_suspect_no_inner_anchor():
    """본문에 anchor 없으면 표시 안 됨."""
    from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import _flag_merge_suspect
    body = "다음 그림은 별의 진화를 나타낸 것이다. " * 50
    questions = [{"number": 13, "text": f"13. {body}", "page_index": 5}]
    _flag_merge_suspect(questions)
    assert "merge_suspect" not in questions[0].get("meta_extra", {})


# ── intent 자동 추정 (title 키워드) ──
# 이 테스트는 인테그레이션 — Django ORM 의존하므로 함수 직접 검증으로 대체.


def test_intent_keywords_present_in_pipeline():
    """run_matchup_pipeline 안의 intent 자동 추정 키워드 sanity check."""
    import inspect
    from apps.worker.ai_worker.ai.pipelines import matchup_pipeline
    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)
    # 시험지 키워드
    for kw in ["시험지", "중간고사", "기말고사", "모의고사", "기출 통과"]:
        assert kw in src, f"시험지 자동 추정 키워드 누락: {kw}"
    # 학습자료 키워드
    for kw in ["메인자료", "복습과제", "객서심화", "개념완성", "WORKBOOK"]:
        assert kw in src, f"학습자료 자동 추정 키워드 누락: {kw}"
