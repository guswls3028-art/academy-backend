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
