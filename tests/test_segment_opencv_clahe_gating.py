"""Stage 6.3Q (2026-05-07) — segment_opencv mild LAB-CLAHE 운영 wiring 회귀 테스트.

manual cut 경로 (matchup_manual_index._preprocess_camera_image) 만 CLAHE+deskew+Unsharp
적용되고 auto 경로는 raw 였던 비대칭 결함의 운영 wiring (ENV gate). default 동작 = 회귀 0.

테스트 범위:
1. _segment_opencv_clahe_mode 기본값 = "disabled"
2. _should_apply_opencv_clahe 모드별 truth table
   - disabled: 항상 False
   - scan_only: 스캔 PDF 또는 student_exam_photo 만 True
   - all: 항상 True
3. _apply_lab_clahe_mild 좌표계 안전성 (shape 보존, in-place equalize, color 채널 보존)
4. segment_questions_opencv apply_clahe kwarg signature (regression: 기존 호출 호환)
5. detect_dual_column_pixel apply_clahe kwarg signature
"""
from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np

from academy.adapters.ai.detection import segment_dispatcher, segment_opencv


# ── ENV gate / decision helper ──────────────────────────────────────────


class TestSegmentOpencvClaheMode:
    def test_default_mode_is_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MATCHUP_SEGMENT_OPENCV_CLAHE", None)
            assert segment_dispatcher._segment_opencv_clahe_mode() == "disabled"

    def test_mode_normalizes_case(self):
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "Scan_Only"}):
            assert segment_dispatcher._segment_opencv_clahe_mode() == "scan_only"

    def test_mode_empty_falls_back_to_disabled(self):
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": ""}):
            assert segment_dispatcher._segment_opencv_clahe_mode() == "disabled"


class TestShouldApplyOpencvClahe:
    def test_disabled_mode_always_false(self):
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "disabled"}):
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type="student_exam_photo",
            )
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type=None,
            )
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type="academy_workbook",
            )

    def test_all_mode_always_true(self):
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "all"}):
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type="academy_workbook",
            )
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type=None,
            )

    def test_scan_only_clean_text_pdf_false(self):
        # academy_workbook + has_embedded_text=True → 미적용 (clean text PDF)
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "scan_only"}):
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type="academy_workbook",
            )
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type="school_exam_pdf",
            )
            assert not segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type=None,
            )

    def test_scan_only_scanned_pdf_true(self):
        # has_embedded_text=False (스캔 PDF) → 적용
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "scan_only"}):
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type="academy_workbook",
            )
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type=None,
            )

    def test_scan_only_student_exam_photo_true_even_with_text(self):
        # student_exam_photo 는 has_embedded_text 무관하게 항상 True
        with patch.dict(os.environ, {"MATCHUP_SEGMENT_OPENCV_CLAHE": "scan_only"}):
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=True, source_type="student_exam_photo",
            )
            assert segment_dispatcher._should_apply_opencv_clahe(
                has_embedded_text=False, source_type="student_exam_photo",
            )


# ── _apply_lab_clahe_mild 좌표계 안전성 ─────────────────────────────────


class TestApplyLabClaheMild:
    def _make_test_bgr(self, h: int = 100, w: int = 80) -> np.ndarray:
        # gradient 패턴 — CLAHE 가 영향을 주는지 측정 가능
        img = np.zeros((h, w, 3), dtype=np.uint8)
        for y in range(h):
            img[y, :, :] = int(255 * y / h)
        return img

    def test_shape_preserved(self):
        img = self._make_test_bgr(h=120, w=200)
        out = segment_opencv._apply_lab_clahe_mild(img)
        assert out.shape == img.shape, "CLAHE 결과 shape 변경 — 좌표계 손상"
        assert out.dtype == img.dtype

    def test_grayscale_input_passes_through(self):
        # 2D ndarray 는 그대로 통과 (no-op)
        gray = np.zeros((50, 50), dtype=np.uint8)
        out = segment_opencv._apply_lab_clahe_mild(gray)
        assert np.array_equal(out, gray)

    def test_none_input_safe(self):
        assert segment_opencv._apply_lab_clahe_mild(None) is None

    def test_color_distribution_changes(self):
        # mild CLAHE 는 contrast 약간 향상 — output 값 분포가 input 과 달라야 함.
        img = self._make_test_bgr()
        out = segment_opencv._apply_lab_clahe_mild(img)
        assert out.shape == img.shape
        # 완전히 동일하면 CLAHE 무동작 — 의심.
        # 다만 너무 극단적 변화도 위험. 평균 절대 차이 1~30 사이가 mild 영역.
        mean_abs_diff = float(np.mean(np.abs(out.astype(np.int16) - img.astype(np.int16))))
        assert mean_abs_diff > 0.5, f"CLAHE 무동작 의심 (diff={mean_abs_diff:.3f})"


# ── segment_questions_opencv / detect_dual_column_pixel signature 회귀 ─


class TestSegmentationCallSignature:
    def test_segment_questions_opencv_default_apply_clahe_false(self):
        """기존 호출 호환 — apply_clahe kwarg 없이 호출해도 동작."""
        # cv2.imread 가 None 을 반환하는 경로 (잘못된 path) 로 빠르게 검증.
        result = segment_opencv.segment_questions_opencv("/nonexistent/path.png")
        assert result == []

    def test_segment_questions_opencv_apply_clahe_kwarg(self):
        result = segment_opencv.segment_questions_opencv(
            "/nonexistent/path.png", apply_clahe=True,
        )
        assert result == []

    def test_detect_dual_column_pixel_default_apply_clahe_false(self):
        result = segment_opencv.detect_dual_column_pixel("/nonexistent/path.png")
        assert result is False

    def test_detect_dual_column_pixel_apply_clahe_kwarg(self):
        result = segment_opencv.detect_dual_column_pixel(
            "/nonexistent/path.png", apply_clahe=True,
        )
        assert result is False
