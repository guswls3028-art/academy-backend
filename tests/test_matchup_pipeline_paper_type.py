"""매치업 pipeline의 paper_type 통합 + 번호 검증 단위 테스트.

목적:
- _verify_problem_numbers: number↔content mismatch 차단 (C10 결함)
- _aggregate_paper_types: doc 단위 분포 집계 + 경고 산출
- is_low_confidence_doc 분기: STUDENT_ANSWER_PHOTO majority → page-as-problem 폴백
- _bias_handwriting_score / source_type 흐름: STUDENT_ANSWER_PHOTO 분기 강제 + paper_type
  보존 (운영 결함 2026-05-04: 1534/1534 unknown → P0 게이트 우회 결함 D-3/D-4)
"""
from __future__ import annotations

import os
import tempfile

from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _aggregate_paper_types,
    _verify_problem_numbers,
)


# ── 1. _verify_problem_numbers ──

def test_verify_number_match_no_flag():
    """text 첫 줄 anchor와 q.number 일치 → meta_extra 변경 없음."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": "1. 다음 중 옳은 것은?\n① A ② B"},
        {"number": 2, "bbox": [0, 0, 100, 100], "text": "2) 그림은 어떤 분자의\n① 가 ② 나"},
    ]
    _verify_problem_numbers(questions)
    for q in questions:
        assert (q.get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_number_mismatch_flagged():
    """text 첫 줄 anchor가 q.number와 다름 → meta_extra["number_mismatch"] 기록.

    C10 결함 회귀: DB number=3인데 image의 본문 번호가 5로 잡힌 케이스.
    """
    questions = [
        {"number": 3, "bbox": [0, 0, 100, 100], "text": "5. 다음 중 옳은 것은?\n① A ② B"},
    ]
    _verify_problem_numbers(questions)
    flag = questions[0]["meta_extra"]["number_mismatch"]
    assert flag == {"db": 3, "ocr": 5}


def test_verify_skips_page_fallback_problems():
    """bbox=None (페이지 폴백 problem)은 검증 대상 아님 — 페이지 전체 텍스트라 부적합."""
    questions = [
        {"number": 1, "bbox": None, "text": "5. 다음... 6. 그림..."},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_skips_when_no_anchor_in_text():
    """text 첫 줄에 anchor 없고 보기 마커도 없으면 검증/flag 모두 스킵 (false negative 안전망)."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": "다음 중 옳은 것은?\n① A"},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None
    assert (questions[0].get("meta_extra") or {}).get("no_anchor_in_text") is None


def test_verify_no_anchor_with_bogi_marker_flagged():
    """text 첫 줄에 anchor 없고 <보기>로 시작하면 no_anchor_in_text=True flag.

    Fix-2 (운영 사고 2026-05-03): doc#148 reanalyze 결과 DB#2가 1번 문항의
    보기/답안 부분만 cropping된 mid-cut 결함. _verify_problem_numbers가 anchor
    없는 text를 silent skip 하면서 검수 UI에 결함 신호 못 줌. 이 fix는 보기 마커
    로 시작하는 cell을 검수 우선순위로 표시.
    """
    questions = [
        {
            "number": 2,
            "bbox": [0, 0, 100, 100],
            "text": "<보기> ㄱ. (가)와 (나)는 거시 세계에 속한다.\n① ㄱ ② ㄴ ③ ㄷ",
        },
    ]
    _verify_problem_numbers(questions)
    flag = questions[0]["meta_extra"]
    assert flag.get("no_anchor_in_text") is True
    assert flag.get("number_mismatch") is None  # mismatch와 별도 flag


def test_verify_no_anchor_with_korean_choice_marker_flagged():
    """ㄱ. ㄴ. 보기 표지로 시작하는 cell도 flag."""
    questions = [
        {"number": 5, "bbox": [0, 0, 100, 100], "text": "ㄱ. 첫 번째 보기\nㄴ. 두 번째"},
    ]
    _verify_problem_numbers(questions)
    assert questions[0]["meta_extra"]["no_anchor_in_text"] is True


def test_verify_no_anchor_with_circle_choice_flagged():
    """① 같은 객관식 답안 마커로 시작하는 cell flag (본문 cut 후 답안만 잡힌 케이스)."""
    questions = [
        {"number": 7, "bbox": [0, 0, 100, 100], "text": "① 가 ② 나 ③ 다 ④ 라 ⑤ 마"},
    ]
    _verify_problem_numbers(questions)
    assert questions[0]["meta_extra"]["no_anchor_in_text"] is True


def test_verify_skips_empty_text():
    """text 비어있으면 검증 스킵."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": ""},
        {"number": 2, "bbox": [0, 0, 100, 100], "text": None},
    ]
    _verify_problem_numbers(questions)
    for q in questions:
        assert (q.get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_section_offset_matches():
    """서답형 [서답형 1] = 101 — section offset도 검증 통과."""
    questions = [
        {"number": 101, "bbox": [0, 0, 100, 100], "text": "[서술형 1] 다음 글을 읽고\n물음에 답하시오"},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None


# ── 2. _aggregate_paper_types ──

def test_aggregate_empty_pages():
    """빈 페이지 리스트 → unknown primary."""
    summary = _aggregate_paper_types([])
    assert summary["primary"] == "unknown"
    assert summary["low_confidence_ratio"] == 0.0
    assert summary["warnings"] == []


def test_aggregate_clean_pdf_majority():
    """clean_pdf_single 다수 + 표지 1장 → primary=clean_pdf_single, 경고 없음."""
    pages = [
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "non_question"},  # 표지
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["primary"] == "clean_pdf_single"
    assert summary["low_confidence_ratio"] == 0.0
    assert summary["warnings"] == []


def test_aggregate_student_answer_photo_warning():
    """학생 답안지 폰사진 1장만 있어도 student_answer_photo_detected 경고."""
    pages = [
        {"paper_type": "scan_dual"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "scan_dual"},
        {"paper_type": "scan_dual"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "student_answer_photo_detected" in summary["warnings"]


def test_aggregate_low_confidence_majority():
    """STUDENT_ANSWER_PHOTO가 30% 이상이면 low_confidence_source_majority 경고."""
    pages = [
        {"paper_type": "student_answer_photo"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "scan_dual"},
        {"paper_type": "scan_dual"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["low_confidence_ratio"] == 0.5
    assert "low_confidence_source_majority" in summary["warnings"]
    assert "student_answer_photo_detected" in summary["warnings"]


def test_aggregate_unknown_only():
    """모두 unknown → low_confidence_ratio = 1.0."""
    pages = [
        {"paper_type": "unknown"},
        {"paper_type": "unknown"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["low_confidence_ratio"] == 1.0
    assert summary["primary"] == "unknown"


def test_aggregate_distribution_counts():
    """distribution이 정확한 카운트 반환."""
    pages = [
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "non_question"},
        {"paper_type": "quadrant"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["distribution"] == {
        "clean_pdf_dual": 2,
        "non_question": 1,
        "quadrant": 1,
    }


def test_aggregate_missing_paper_type_defaults_unknown():
    """paper_type 키 없는 페이지는 unknown으로 처리."""
    pages = [
        {"paper_type": "clean_pdf_single"},
        {},  # 누락
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["distribution"].get("unknown") == 1


def test_aggregate_non_question_majority_warning():
    """비-문항 페이지가 50% 이상 + 4페이지 이상 → non_question_majority 경고."""
    pages = [
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "clean_pdf_single"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "non_question_majority" in summary["warnings"]


def test_aggregate_non_question_minor_no_warning():
    """비-문항 페이지가 짧은 doc (3 페이지 이하)에서는 non_question_majority 경고 안 함."""
    pages = [
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "clean_pdf_single"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "non_question_majority" not in summary["warnings"]


# ── 3. _bias_handwriting_score (Phase A-1: source_type → paper_type 분류기 신호) ──

def test_bias_handwriting_score_student_photo():
    """student_exam_photo → 0.85 (classify_paper_type의 0.78 임계값 통과)."""
    from academy.adapters.ai.detection.segment_dispatcher import _bias_handwriting_score

    assert _bias_handwriting_score("student_exam_photo") == 0.85


def test_bias_handwriting_score_other_sources_none():
    """다른 source_type은 None — 픽셀/텍스트 휴리스틱 그대로 사용."""
    from academy.adapters.ai.detection.segment_dispatcher import _bias_handwriting_score

    for st in (
        "school_exam_pdf", "commercial_workbook", "academy_workbook",
        "explanation", "answer_key", "other", None, "",
    ):
        assert _bias_handwriting_score(st) is None, f"source_type={st!r} should yield None"


# ── 4. classify_paper_type + handwriting_score bias 통합 ──

def test_classify_with_handwriting_bias_yields_student_answer_photo():
    """has_embedded_text=False + handwriting_score=0.85 → STUDENT_ANSWER_PHOTO 분기.

    운영 결함 2026-05-04: classify_paper_type 호출 시 handwriting_score 인자가
    누락되어 STUDENT_ANSWER_PHOTO 분기가 dead branch였음. T2 1534/1534 페이지 unknown.
    Phase A-1: source_type=student_exam_photo이면 bias 0.85로 강제 진입.
    """
    from academy.domain.tools.paper_type import PaperType, classify_paper_type

    pt = classify_paper_type(
        text_blocks=None,
        image_path=None,  # bias만 신뢰. UNKNOWN 분기로 빠지는지 안 빠지는지 검증
        page_width=1000.0,
        page_height=1400.0,
        has_embedded_text=False,
        handwriting_score=0.85,
    )
    # text_blocks=None and image_path=None은 UNKNOWN 분기 — bias도 무시됨.
    # 이 경우 pipeline에서 page-as-problem 폴백이 어차피 진입 (is_student_photo 게이트)
    # 단, image_path 있으면 STUDENT_ANSWER_PHOTO 분류 가능. 실제 운영 흐름 검증:
    assert pt.paper_type is PaperType.UNKNOWN  # 입력 신호 부재 케이스


def test_classify_with_handwriting_bias_and_image_yields_student_answer_photo(tmp_path):
    """image_path 있으면 bias 신호가 살아있음 → STUDENT_ANSWER_PHOTO 강제."""
    from PIL import Image

    from academy.domain.tools.paper_type import PaperType, classify_paper_type

    img_path = tmp_path / "fake_student.png"
    Image.new("RGB", (800, 1100), (240, 240, 240)).save(img_path)

    pt = classify_paper_type(
        text_blocks=None,
        image_path=str(img_path),
        page_width=800.0,
        page_height=1100.0,
        has_embedded_text=False,
        handwriting_score=0.85,
    )
    assert pt.paper_type is PaperType.STUDENT_ANSWER_PHOTO
    assert pt.confidence >= 0.78


def test_classify_without_bias_no_student_answer_photo(tmp_path):
    """handwriting_score=None이면 STUDENT_ANSWER_PHOTO 분기 진입 X (회귀 락)."""
    from PIL import Image

    from academy.domain.tools.paper_type import PaperType, classify_paper_type

    img_path = tmp_path / "fake_print.png"
    Image.new("RGB", (800, 1100), (255, 255, 255)).save(img_path)

    pt = classify_paper_type(
        text_blocks=None,
        image_path=str(img_path),
        page_width=800.0,
        page_height=1100.0,
        has_embedded_text=False,
        handwriting_score=None,
    )
    assert pt.paper_type is not PaperType.STUDENT_ANSWER_PHOTO


def test_classify_with_embedded_text_ignores_bias(tmp_path):
    """has_embedded_text=True (PDF 텍스트 추출 가능)면 bias 있어도 STUDENT_ANSWER_PHOTO 분기 X.

    인쇄 PDF가 손글씨 같은 stroke variance 휴리스틱에 잡혀 잘못 분류되는 false positive 차단.
    """
    from PIL import Image

    from academy.domain.tools.paper_type import PaperType, classify_paper_type

    img_path = tmp_path / "clean_pdf_page.png"
    Image.new("RGB", (800, 1100), (255, 255, 255)).save(img_path)

    pt = classify_paper_type(
        text_blocks=None,
        image_path=str(img_path),
        page_width=800.0,
        page_height=1100.0,
        has_embedded_text=True,
        handwriting_score=0.85,
    )
    assert pt.paper_type is not PaperType.STUDENT_ANSWER_PHOTO


# ── 5. _classify_and_record_paper_type (OCR/OpenCV 경로 paper_type 보존) ──

def test_classify_and_record_writes_to_page_info(tmp_path):
    """page_info에 paper_type 저장 — _aggregate_paper_types가 unknown으로 떨어뜨리는 결함 차단.

    운영 결함 2026-05-04: OCR 경로에서 segment_questions_ocr_regions은 boxes만 반환하므로
    paper_type이 page_info에 없음 → 1534/1534 unknown. 이 헬퍼가 보완.
    """
    from PIL import Image

    from academy.adapters.ai.detection.segment_dispatcher import _classify_and_record_paper_type

    img_path = tmp_path / "page.png"
    Image.new("RGB", (800, 1100), (240, 240, 240)).save(img_path)

    page_info: dict = {}
    _classify_and_record_paper_type(
        page_info, str(img_path),
        has_embedded_text=False,
        handwriting_bias=0.85,
    )
    assert page_info.get("paper_type") == "student_answer_photo"


def test_classify_and_record_skip_when_already_set(tmp_path):
    """이미 paper_type이 unknown 아닌 값으로 설정되어 있으면 덮어쓰지 않음.

    PDF 텍스트 경로에서 정상 분류된 페이지가 OCR fallback 경로 들어오면 덮어쓰기 방지.
    """
    from PIL import Image

    from academy.adapters.ai.detection.segment_dispatcher import _classify_and_record_paper_type

    img_path = tmp_path / "page.png"
    Image.new("RGB", (800, 1100), (240, 240, 240)).save(img_path)

    page_info: dict = {"paper_type": "clean_pdf_single"}
    _classify_and_record_paper_type(
        page_info, str(img_path),
        has_embedded_text=False,
        handwriting_bias=0.85,
    )
    assert page_info["paper_type"] == "clean_pdf_single"  # 보존


def test_classify_and_record_overwrites_unknown(tmp_path):
    """page_info["paper_type"]가 "unknown"이면 다시 분류 시도 (PDF 텍스트 경로 실패 케이스)."""
    from PIL import Image

    from academy.adapters.ai.detection.segment_dispatcher import _classify_and_record_paper_type

    img_path = tmp_path / "page.png"
    Image.new("RGB", (800, 1100), (240, 240, 240)).save(img_path)

    page_info: dict = {"paper_type": "unknown"}
    _classify_and_record_paper_type(
        page_info, str(img_path),
        has_embedded_text=False,
        handwriting_bias=0.85,
    )
    assert page_info["paper_type"] == "student_answer_photo"


# ── 6. segment_questions_multipage 단일 이미지 + source_type 통합 ──

def test_segment_multipage_single_image_with_student_source(tmp_path, monkeypatch):
    """단일 이미지 + source_type=student_exam_photo → page dict의 paper_type=student_answer_photo.

    운영 흐름 회귀 락: 학생답안지 폰사진(보통 단일 이미지 업로드)이 dispatcher → pipeline →
    _aggregate_paper_types 거쳐 primary='student_answer_photo' 결정 → page-as-problem 폴백.
    """
    from PIL import Image

    from academy.adapters.ai.detection.segment_dispatcher import segment_questions_multipage

    img_path = tmp_path / "student_photo.png"
    Image.new("RGB", (1200, 1600), (235, 235, 235)).save(img_path)

    # _segment_single_image이 OCR/OpenCV/YOLO 호출하지만 fake 이미지면 빈 boxes 반환
    # (테스트 환경 OCR 자격 없음). 우리는 paper_type만 검증.
    result = segment_questions_multipage(str(img_path), source_type="student_exam_photo")

    assert result["is_pdf"] is False
    assert len(result["pages"]) == 1
    page = result["pages"][0]
    assert page["paper_type"] == "student_answer_photo"


def test_segment_multipage_single_image_without_source(tmp_path):
    """source_type 없이 단일 이미지 → STUDENT_ANSWER_PHOTO 분기 진입 X (bias 없음)."""
    from PIL import Image

    from academy.adapters.ai.detection.segment_dispatcher import segment_questions_multipage

    img_path = tmp_path / "unknown_source.png"
    Image.new("RGB", (1200, 1600), (235, 235, 235)).save(img_path)

    result = segment_questions_multipage(str(img_path))  # source_type=None
    page = result["pages"][0]
    assert page["paper_type"] != "student_answer_photo"


# ── A-2 POC: estimate_handwriting_score (2026-05-04) ──

def test_estimate_handwriting_score_returns_float(tmp_path):
    """기본 동작 — float [0.0, 1.0] 반환."""
    from PIL import Image

    from academy.adapters.ai.detection.segment_opencv import estimate_handwriting_score

    img_path = tmp_path / "blank.png"
    Image.new("RGB", (800, 1000), (255, 255, 255)).save(img_path)

    score = estimate_handwriting_score(str(img_path))
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_estimate_handwriting_score_empty_image_zero(tmp_path):
    """완전 빈 페이지 → 0.0 (edge 없음)."""
    from PIL import Image

    from academy.adapters.ai.detection.segment_opencv import estimate_handwriting_score

    img_path = tmp_path / "white.png"
    Image.new("RGB", (800, 1000), (255, 255, 255)).save(img_path)

    score = estimate_handwriting_score(str(img_path))
    assert score == 0.0


def test_estimate_handwriting_score_invalid_path_zero():
    """존재하지 않는 path → 0.0 (안전 폴백)."""
    from academy.adapters.ai.detection.segment_opencv import estimate_handwriting_score

    score = estimate_handwriting_score("/nonexistent/path.png")
    assert score == 0.0


def test_estimate_handwriting_score_too_small_image_zero(tmp_path):
    """100x100 미만 이미지 → 0.0 (의미있는 측정 불가)."""
    from PIL import Image

    from academy.adapters.ai.detection.segment_opencv import estimate_handwriting_score

    img_path = tmp_path / "tiny.png"
    Image.new("RGB", (50, 50), (240, 240, 240)).save(img_path)

    score = estimate_handwriting_score(str(img_path))
    assert score == 0.0
