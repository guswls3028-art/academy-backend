"""source_type_derive 단위 테스트 (Phase 1, 2026-05-09)."""
from __future__ import annotations

import pytest

from academy.domain.tools.source_type_derive import derive_source_type_from_paper_type


class TestDeriveSourceType:
    def test_student_answer_photo_always_maps_to_student_exam_photo(self):
        # 100% 매핑 — 사용자가 명시 specific 안 했어도 학생 폰사진은 100%.
        assert derive_source_type_from_paper_type("student_answer_photo", "other") == "student_exam_photo"
        assert derive_source_type_from_paper_type("student_answer_photo", None) == "student_exam_photo"
        assert derive_source_type_from_paper_type("student_answer_photo", "") == "student_exam_photo"

    def test_student_answer_photo_skip_when_already_aligned(self):
        # 이미 정합 — None 반환.
        assert derive_source_type_from_paper_type("student_answer_photo", "student_exam_photo") is None

    def test_specific_source_type_protected(self):
        # filename heuristic 또는 user 가 specific value 골랐으면 보호 (학생 폰사진 제외).
        assert derive_source_type_from_paper_type("scan_dual", "school_exam_pdf") is None
        assert derive_source_type_from_paper_type("clean_pdf_dual", "commercial_workbook") is None
        assert derive_source_type_from_paper_type("side_notes", "academy_workbook") is None
        assert derive_source_type_from_paper_type("non_question", "explanation") is None

    def test_scan_pages_default_to_school_exam_pdf(self):
        # cur="other" 또는 빈 값일 때만 보정.
        assert derive_source_type_from_paper_type("scan_single", "other") == "school_exam_pdf"
        assert derive_source_type_from_paper_type("scan_dual", "") == "school_exam_pdf"
        assert derive_source_type_from_paper_type("scan_single", None) == "school_exam_pdf"

    def test_side_notes_default_to_academy_workbook(self):
        assert derive_source_type_from_paper_type("side_notes", "other") == "academy_workbook"

    def test_ambiguous_paper_types_return_none(self):
        # clean_pdf 시리즈는 학교/시판/학원 구분 불가. quadrant 표본 부족. 보정 안 함.
        assert derive_source_type_from_paper_type("clean_pdf_single", "other") is None
        assert derive_source_type_from_paper_type("clean_pdf_dual", "other") is None
        assert derive_source_type_from_paper_type("quadrant", "other") is None
        assert derive_source_type_from_paper_type("non_question", "other") is None
        assert derive_source_type_from_paper_type("unknown", "other") is None

    def test_empty_paper_type_returns_none(self):
        assert derive_source_type_from_paper_type(None, "other") is None
        assert derive_source_type_from_paper_type("", "other") is None
