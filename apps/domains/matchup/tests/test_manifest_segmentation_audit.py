from django.test import SimpleTestCase

from apps.domains.matchup.management.commands.matchup_manifest_segmentation_audit import (
    _manifest_quality_grade,
    _select_documents,
    _structural_flags,
)


class ManifestSegmentationAuditHelperTests(SimpleTestCase):
    def test_select_documents_defaults_to_non_photo_targets(self):
        manifest = {
            "documents": [
                {"id": 1, "target_non_photo": True, "meta_source_type": "academy_workbook"},
                {"id": 2, "target_non_photo": False, "meta_source_type": "student_exam_photo"},
                {"id": 3, "target_non_photo": True, "meta_source_type": "student_exam_photo"},
                {"id": 4, "target_non_photo": False, "meta_source_type": "other"},
            ],
        }

        selected = _select_documents(
            manifest,
            doc_ids=set(),
            include_student_photo=False,
            include_non_target=False,
        )

        self.assertEqual([doc["id"] for doc in selected], [1])

    def test_structural_flags_fail_empty_positive_document(self):
        flags = _structural_flags(
            doc={"problem_rows": 12, "paper_primary": "clean_pdf_dual"},
            metric={"total_boxes": 0, "numbered_box_count": 0, "unnumbered_box_count": 0},
            gt_metric=None,
        )

        self.assertIn("expected_positive_no_boxes", flags)
        self.assertIn("severe_under_expected_count", flags)
        self.assertEqual(_manifest_quality_grade(flags)["status"], "fail")

    def test_structural_flags_warn_under_count_without_severe_drop(self):
        flags = _structural_flags(
            doc={"problem_rows": 100, "paper_primary": "clean_pdf_dual"},
            metric={"total_boxes": 85, "numbered_box_count": 85, "unnumbered_box_count": 0},
            gt_metric=None,
        )

        self.assertEqual(flags, ["under_expected_count"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_fail_non_question_expected_empty_has_boxes(self):
        flags = _structural_flags(
            doc={"problem_rows": 0, "paper_primary": "answer_key"},
            metric={"total_boxes": 2, "numbered_box_count": 2, "unnumbered_box_count": 0},
            gt_metric=None,
        )

        self.assertEqual(flags, ["non_question_expected_empty_has_boxes"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "fail")
