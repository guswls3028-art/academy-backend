from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from academy.application.use_cases.ai.pipelines import matchup_pipeline
from academy.domain.tools.paper_type import PaperType, PaperTypeResult
from academy.domain.tools.question_splitter import TextBlock, split_questions


class HybridVlmSchoolExamSafetyTests(TestCase):
    def test_school_exam_pdf_preserves_ocr_numbered_questions(self):
        questions = [
            {
                "number": 3,
                "page_index": 0,
                "bbox": [1010, 360, 1014, 249],
                "image_path": "/tmp/page_000.png",
                "meta_extra": {"number_source": "segment"},
            },
            {
                "number": 12,
                "page_index": 2,
                "bbox": [0, 1585, 1014, 549],
                "image_path": "/tmp/page_002.png",
                "meta_extra": {"number_source": "segment"},
            },
            {
                "number": 102,
                "page_index": 6,
                "bbox": [0, 1343, 1014, 289],
                "image_path": "/tmp/page_006.png",
                "meta_extra": {"number_source": "segment"},
            },
        ]

        with patch(
            "academy.adapters.ai.detection.hybrid_vlm_classifier.filter_questions_by_hybrid_vlm",
            return_value=([], {"rejected": 3}),
        ) as hybrid_filter:
            result = matchup_pipeline._filter_questions_by_hybrid_vlm(
                questions,
                source_type="school_exam_pdf",
                document_id=817,
                tenant_id=1,
            )

        self.assertEqual(result, questions)
        hybrid_filter.assert_not_called()


class ContinuousScanSplitterSafetyTests(TestCase):
    def test_keeps_later_right_column_exam_anchors_after_dense_subrows(self):
        page_width = 2024.0
        page_height = 2866.0
        paper_type = PaperTypeResult(
            paper_type=PaperType.SCAN_DUAL,
            confidence=1.0,
            is_dual_column=True,
            is_quadrant=False,
            is_handwriting_present=False,
            has_embedded_text=False,
            debug={"is_dual_text": True},
        )
        blocks = [
            TextBlock("10. 그림은 세슘 원자 시계를 나타낸 것이다.", 162, 363, 727, 391),
            TextBlock("11. 그림 (가)는 핵산이고, 그림 (나)는 적혈구이다.", 167, 1274, 799, 1303),
            TextBlock("12. 그림 (가)와 (나)는 지구의 크기를 측정하는 방법을 나타낸 것이다.", 166, 2099, 999, 2129),
            TextBlock("13. 다음은 어떤 지역의 기상 정보이다.", 1045, 725, 1539, 753),
            TextBlock("1. 국제 단위계(SI)의 기본 단위에 C가 포함되어 있다.", 1066, 1329, 1757, 1357),
            TextBlock("4. 바람의 속력의 단위는 국제 단위계(SI) 기본 단위이다.", 1067, 1374, 1783, 1404),
            TextBlock("14. 다음 중 유도량과 유도 단위를 옳게 짝지은 것은?", 1044, 1640, 1797, 1669),
            TextBlock("15. 다음은 일기 예보의 일부이다.", 1043, 1944, 1463, 1975),
            TextBlock("4. 이에 해당하는 단위는 ℃이다.", 1052, 2474, 1494, 2511),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=2,
            paper_type=paper_type,
        )

        self.assertEqual([region.number for region in regions], [10, 11, 12, 13, 14, 15])


class CleanPdfWideLayoutSplitterTests(TestCase):
    def test_dual_hint_does_not_half_crop_full_width_visual_questions(self):
        page_width = 612.0
        page_height = 864.0
        paper_type = PaperTypeResult(
            paper_type=PaperType.CLEAN_PDF_DUAL,
            confidence=0.9,
            is_dual_column=True,
            is_quadrant=False,
            is_handwriting_present=False,
            has_embedded_text=True,
            debug={"is_dual_text": True},
        )
        blocks = [
            TextBlock("35.", 70.9, 154.0, 94.0, 168.8),
            TextBlock("그림은 중심부의 핵융합 반응이 끝난 두 별의 내부 구조를 나타낸 것이다.", 111.0, 154.0, 570.0, 169.0),
            TextBlock("(가)", 255.0, 330.0, 278.0, 345.0),
            TextBlock("(나)", 430.0, 330.0, 452.0, 345.0),
            TextBlock("이에 대한 설명으로 옳은 것만을 보기에서 있는 대로 고른 것은?", 74.0, 355.0, 528.0, 371.0),
            TextBlock("보기 ㄱ. 질량은 (가)가 (나)보다 크다. ㄴ. 중심부의 온도는 다르다.", 72.0, 391.0, 520.0, 438.0),
            TextBlock("36.", 70.9, 463.7, 94.0, 478.5),
            TextBlock("그림 (가)는 별의 내부 구조를, (나)는 전자 배치 모형을 나타낸 것이다.", 111.0, 463.7, 565.0, 481.0),
            TextBlock("(가)", 252.0, 710.0, 275.0, 725.0),
            TextBlock("(나)", 425.0, 710.0, 447.0, 725.0),
            TextBlock("ㄱ. 내부 온도는 중심에서 표면으로 갈수록 높아진다. ㄴ. 질량은 태양보다 크다.", 72.0, 744.0, 522.0, 790.0),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=37,
            paper_type=paper_type,
        )

        self.assertEqual([region.number for region in regions], [35, 36])
        for region in regions:
            width_ratio = (region.bbox[2] - region.bbox[0]) / page_width
            self.assertGreaterEqual(width_ratio, 0.65)


class CropUploadColumnClipTests(TestCase):
    def test_column_clip_is_skipped_for_already_wide_bbox(self):
        self.assertFalse(
            matchup_pipeline._should_clip_crop_padding_to_column(
                bbox_width=1463,
                image_width=1700,
                column_count=2,
            )
        )

    def test_column_clip_is_kept_for_normal_column_bbox(self):
        self.assertTrue(
            matchup_pipeline._should_clip_crop_padding_to_column(
                bbox_width=720,
                image_width=1700,
                column_count=2,
            )
        )

    def test_padding_does_not_cross_next_question_top(self):
        capped_h = matchup_pipeline._cap_padded_crop_to_next_question(
            y=342,
            h=1015,
            original_bottom=1228,
            next_top=1229,
        )

        self.assertEqual(capped_h, 886)

    def test_existing_overlap_is_not_hidden_by_padding_cap(self):
        capped_h = matchup_pipeline._cap_padded_crop_to_next_question(
            y=342,
            h=1015,
            original_bottom=1240,
            next_top=1229,
        )

        self.assertEqual(capped_h, 1015)

    def test_next_crop_limit_uses_same_column_not_other_column(self):
        limits = matchup_pipeline._next_crop_top_limits(
            [
                {
                    "image_path": "page-12.png",
                    "bbox": [97, 372, 702, 952],
                },
                {
                    "image_path": "page-12.png",
                    "bbox": [97, 1339, 708, 402],
                },
                {
                    "image_path": "page-12.png",
                    "bbox": [844, 452, 550, 528],
                },
                {
                    "image_path": "page-12.png",
                    "bbox": [844, 1338, 686, 372],
                },
            ]
        )

        self.assertEqual(limits[0], 1339)
        self.assertEqual(limits[2], 1338)

    def test_next_crop_limit_still_caps_full_width_questions(self):
        limits = matchup_pipeline._next_crop_top_limits(
            [
                {
                    "image_path": "page-37.png",
                    "bbox": [21, 368, 1463, 860],
                },
                {
                    "image_path": "page-37.png",
                    "bbox": [176, 1229, 1330, 967],
                },
            ]
        )

        self.assertEqual(limits[0], 1229)
