from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.views.omr_document_views import _parse_omr_params


class EssayNumberingTests(SimpleTestCase):
    def test_separate_labels_preserve_contract_numbers(self):
        doc = OMRDocument(
            exam_title="학교 모의고사",
            mc_count=18,
            essay_count=2,
            choice_question_numbers=tuple(range(1, 19)),
            essay_question_numbers=(19, 20),
            essay_numbering="separate",
        )
        self.assertEqual(doc.resolved_essay_question_numbers, (19, 20))
        self.assertEqual(doc.display_essay_question_numbers, (1, 2))
        self.assertEqual(
            [row["number"] for row in OMRHtmlRenderer()._build_essay_rows(doc)],
            [1, 2],
        )

    def test_default_labels_and_invalid_tool_setting(self):
        doc = OMRDocument(exam_title="기존 시험", mc_count=18, essay_count=2)
        self.assertEqual(doc.display_essay_question_numbers, (19, 20))
        with self.assertRaises(ValidationError):
            _parse_omr_params({"essay_numbering": "unknown"})
