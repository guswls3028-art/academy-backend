"""Stage 5.4 (2026-05-06) — Tier 0 v4 단위 테스트.

검증 항목:
- _FILENAME_HINTS_V4 추가 키워드 (중철물 / 기본량 / 학년도 등)
- classify_paper_type_v4: v4 키워드 우선
- doc_level_dedup_anchors: 학습자료 / 시험지 분기
- analyze_pdf_v4: 통합 + expected_max + doc_dedup 결과 schema
- regression: v3 / v2 / v1 모두 callable
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.tools.pymupdf_renderer import create_pdf_file
from academy.adapters.ai.detection.tier0_native_pdf import (
    _FILENAME_HINTS_V4,
    _PAPER_TYPE_EXPECTED_MAX,
    PAPER_TYPE_ADVANCED_MATERIAL,
    PAPER_TYPE_ANSWER_EXPLANATION,
    PAPER_TYPE_COVER,
    PAPER_TYPE_EXAM,
    PAPER_TYPE_KILLER_TEST,
    PAPER_TYPE_MOCK_EXAM,
    PAPER_TYPE_REVIEW_HOMEWORK,
    PAPER_TYPE_UNKNOWN,
    PAPER_TYPE_WORKBOOK_MAIN,
    NumberAnchor,
    classify_paper_type_v4,
    doc_level_dedup_anchors,
)


def _anchor(n, page_idx=0, x0=50, y0=100):
    return NumberAnchor(
        number=n, page_index=page_idx,
        bbox=(float(x0), float(y0), float(x0 + 20), float(y0 + 15)),
        text=f"{n}.", style="arabic_dot", confidence=0.9,
    )


# ── classify_paper_type_v4 ──


class ClassifyPaperTypeV4Tests(TestCase):
    def test_jungchulmul_keyword_v4(self):
        """v4 신규 — '중철물' → workbook_main."""
        pt, conf, debug = classify_paper_type_v4(
            file_name="1-1-1 과학의 기본량 중대부고 중철물 2026.pdf",
        )
        self.assertEqual(pt, PAPER_TYPE_WORKBOOK_MAIN)
        self.assertEqual(debug.get("filename_match_v4"), "중철물")
        self.assertGreaterEqual(conf, 0.8)

    def test_gibon_yang_keyword_v4(self):
        pt, _, _ = classify_paper_type_v4(file_name="과학의 기본량 자료.pdf")
        self.assertEqual(pt, PAPER_TYPE_WORKBOOK_MAIN)

    def test_hwakhakgyeolhap_keyword_v4(self):
        """공백 없는 화학결합."""
        pt, _, _ = classify_paper_type_v4(file_name="중대부고 화학결합 2026.pdf")
        self.assertEqual(pt, PAPER_TYPE_WORKBOOK_MAIN)

    def test_hakneondo_keyword_exam(self):
        """v4 — '학년도' 추가 hint → exam (기존 '중간고사' 외에)."""
        pt, _, _ = classify_paper_type_v4(file_name="2024학년도 1학기 어떤 시험지.pdf")
        # 기존 v3 hint "중간고사" 도 있어서 exam 분류 — v4 hint 우선
        self.assertEqual(pt, PAPER_TYPE_EXAM)

    def test_v3_hints_still_work_through_v4(self):
        """v4 가 v3 hints 와도 호환."""
        # 기존 v3 키워드 — 모의고사
        pt, _, _ = classify_paper_type_v4(file_name="신민 모의고사 내신용.pdf")
        self.assertEqual(pt, PAPER_TYPE_MOCK_EXAM)

    def test_unknown_when_no_signals_v4(self):
        pt, _, _ = classify_paper_type_v4(file_name="random.pdf")
        self.assertEqual(pt, PAPER_TYPE_UNKNOWN)


# ── doc_level_dedup_anchors ──


class DocLevelDedupTests(TestCase):
    def test_exam_paper_no_dedup(self):
        """시험지/모의고사/킬러는 dedup 안 함 (정상 시퀀스)."""
        per_page = [
            [_anchor(i, page_idx=0) for i in (1, 2, 3)],
            [_anchor(i, page_idx=1) for i in (4, 5, 6)],
        ]
        result, debug = doc_level_dedup_anchors(per_page, PAPER_TYPE_EXAM)
        self.assertEqual(result, per_page)
        self.assertFalse(debug["applied"])

    def test_answer_or_cover_no_dedup(self):
        per_page = [[_anchor(i) for i in (1, 2, 3)]]
        for pt in (PAPER_TYPE_ANSWER_EXPLANATION, PAPER_TYPE_COVER):
            result, debug = doc_level_dedup_anchors(per_page, pt)
            self.assertEqual(result, per_page)

    def test_learning_material_dedup_when_high_duplicate_ratio(self):
        """학습자료 — 같은 number 여러 페이지 반복 → dedup 적용."""
        # 1, 2, 3 이 5페이지에 반복
        per_page = [
            [_anchor(i, page_idx=p) for i in (1, 2, 3)]
            for p in range(5)
        ]
        result, debug = doc_level_dedup_anchors(per_page, PAPER_TYPE_REVIEW_HOMEWORK)
        self.assertTrue(debug["applied"])
        # 첫 page 만 1, 2, 3 유지, 나머지 page 는 비어야
        self.assertEqual([a.number for a in result[0]], [1, 2, 3])
        for p in range(1, 5):
            self.assertEqual(result[p], [])
        self.assertEqual(debug["duplicates_removed"], 12)  # 4 페이지 × 3 = 12

    def test_workbook_dedup_when_over_ratio_high(self):
        """workbook_main — over_ratio 3.0+ 면 dedup (duplicates 적어도)."""
        # workbook expected_max=80. 250 anchor → over_ratio=3.125 (>=3.0)
        per_page = [
            [_anchor(i, page_idx=p) for i in range(1, 51)]  # 50개씩
            for p in range(5)
        ]
        result, debug = doc_level_dedup_anchors(per_page, PAPER_TYPE_WORKBOOK_MAIN)
        self.assertTrue(debug["applied"])
        # 첫 page 만 1~50 유지
        self.assertEqual(len(result[0]), 50)
        for p in range(1, 5):
            self.assertEqual(result[p], [])

    def test_no_dedup_when_low_duplicate_ratio(self):
        """학습자료여도 duplicate 거의 없으면 dedup 안 함."""
        per_page = [
            [_anchor(1, page_idx=0)],
            [_anchor(2, page_idx=1)],
            [_anchor(3, page_idx=2)],
        ]
        result, debug = doc_level_dedup_anchors(per_page, PAPER_TYPE_REVIEW_HOMEWORK)
        # duplicate_ratio = 0/3 = 0, over_ratio = 3/60 = 0.05 — dedup 미적용
        self.assertFalse(debug["applied"])
        self.assertEqual(result, per_page)

    def test_unknown_paper_type_dedup_when_high_duplicate(self):
        """unknown 도 학습자료처럼 dedup 적용 (보수적)."""
        per_page = [
            [_anchor(i, page_idx=p) for i in (1, 2, 3)]
            for p in range(5)
        ]
        result, debug = doc_level_dedup_anchors(per_page, PAPER_TYPE_UNKNOWN)
        self.assertTrue(debug["applied"])

    def test_paper_type_expected_max_table(self):
        """expected_max 정의 검증 — 운영 _MAX_LEGIT_QUESTION_NUMBER=60 미만/근처."""
        for pt in (PAPER_TYPE_EXAM, PAPER_TYPE_MOCK_EXAM, PAPER_TYPE_KILLER_TEST):
            self.assertLessEqual(_PAPER_TYPE_EXPECTED_MAX[pt], 60)
        # 학습자료는 본문 항목 포함 → 더 크게
        self.assertGreaterEqual(_PAPER_TYPE_EXPECTED_MAX[PAPER_TYPE_ADVANCED_MATERIAL], 60)
        # answer/cover 는 0
        self.assertEqual(_PAPER_TYPE_EXPECTED_MAX[PAPER_TYPE_ANSWER_EXPLANATION], 0)
        self.assertEqual(_PAPER_TYPE_EXPECTED_MAX[PAPER_TYPE_COVER], 0)


class AnalyzePdfV4IntegrationTests(TestCase):
    def _make_workbook_pdf_with_repeat(self):
        """workbook_main 분류되는 파일명 + 페이지마다 1, 2, 3 반복 (학습자료 시뮬)."""
        return create_pdf_file(
            pages=[
                [
                    (50, 100, "1. 다음 중 옳은 것 ① ② ③", 10),
                    (50, 300, "2. 그림에서 ① ② ③", 10),
                    (50, 500, "3. 보기에서 ① ② ③", 10),
                ]
                for _ in range(5)
            ],
            suffix="_복습과제_test.pdf",
        )

    def test_v4_classifies_review_homework(self):
        """v4 통합 — review_homework 파일명 분류 + doc_dedup 결과 schema 포함.

        PyMuPDF in-memory 페이지의 line_start anchor 검출은 layout-dependent 라
        deterministic 단언은 unit test (doc_level_dedup_anchors 직접 호출) 에서.
        여기서는 통합 흐름 / output schema 만 검증.
        """
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v4
        import os
        pdf = self._make_workbook_pdf_with_repeat()
        try:
            result = analyze_pdf_v4(pdf, file_name=os.path.basename(pdf))
            self.assertEqual(result["version"], "v4")
            self.assertEqual(result["paper_type"], PAPER_TYPE_REVIEW_HOMEWORK)
            self.assertIn("doc_dedup", result)
            # doc_dedup schema
            self.assertIn("applied", result["doc_dedup"])
            self.assertIn("duplicate_ratio", result["doc_dedup"])
            self.assertIn("over_ratio", result["doc_dedup"])
        finally:
            os.unlink(pdf)

    def test_v4_output_has_expected_max(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v4
        import os
        pdf = self._make_workbook_pdf_with_repeat()
        try:
            result = analyze_pdf_v4(pdf)
            self.assertIn("expected_max", result)
            self.assertEqual(result["expected_max"], _PAPER_TYPE_EXPECTED_MAX[result["paper_type"]])
        finally:
            os.unlink(pdf)


class V4RegressionTests(TestCase):
    def test_v1_v2_v3_v4_callable(self):
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            classify_paper_type_prototype, classify_paper_type_v4,
            detect_problem_anchors, detect_problem_anchors_v2, detect_problem_anchors_v3,
        )
        for fn in (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            classify_paper_type_prototype, classify_paper_type_v4,
            detect_problem_anchors, detect_problem_anchors_v2, detect_problem_anchors_v3,
        ):
            self.assertTrue(callable(fn))

    def test_v4_does_not_import_orm(self):
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "from apps.domains.matchup.models",
            "MatchupProblem",
            "ProblemSegmentationProposal",
            ".objects.",
            ".save(",
            ".delete(",
            ".bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(token, src, f"v4 추가 후 forbidden token '{token}'")

    def test_filename_hints_v4_complete(self):
        """_FILENAME_HINTS_V4 에 핵심 운영 키워드 포함."""
        keywords = {kw for kw, _ in _FILENAME_HINTS_V4}
        self.assertIn("중철물", keywords)
        self.assertIn("기본량", keywords)
        self.assertIn("화학결합", keywords)
