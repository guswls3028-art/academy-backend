from django.test import SimpleTestCase

from apps.domains.matchup.management.commands.matchup_manifest_segmentation_audit import (
    _manifest_quality_grade,
    _select_documents,
    _structural_flags,
)
from apps.domains.matchup.management.commands.matchup_manual_gt_eval import (
    GroundTruthBox,
    NormBox,
    PredictedBox,
    evaluate_predictions_against_gt,
    _extract_predictions,
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

    def test_structural_flags_warn_text_sheet_without_question_markers(self):
        flags = _structural_flags(
            doc={"problem_rows": 5, "paper_primary": "clean_pdf_dual"},
            metric={
                "total_boxes": 0,
                "page_count": 1,
                "text_page_count": 1,
                "question_marker_count": 0,
                "numbered_box_count": 0,
                "unnumbered_box_count": 0,
            },
            gt_metric=None,
        )

        self.assertIn("expected_count_without_question_markers", flags)
        self.assertIn("manifest_expected_count_drift", flags)
        self.assertNotIn("expected_positive_no_boxes", flags)
        self.assertNotIn("severe_under_expected_count", flags)
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_warn_under_count_without_severe_drop(self):
        flags = _structural_flags(
            doc={"problem_rows": 100, "paper_primary": "clean_pdf_dual"},
            metric={"total_boxes": 85, "numbered_box_count": 85, "unnumbered_box_count": 0},
            gt_metric=None,
        )

        self.assertEqual(flags, ["under_expected_count"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_demote_expected_drift_when_physical_gt_recall_complete(self):
        flags = _structural_flags(
            doc={"problem_rows": 100, "paper_primary": "clean_pdf_dual"},
            metric={
                "total_boxes": 40,
                "numbered_box_count": 40,
                "unnumbered_box_count": 0,
            },
            gt_metric={
                "missed_count": 4,
                "physical_gt_count": 40,
                "physical_missed_count": 0,
                "precision": 1.0,
            },
        )

        self.assertEqual(flags, ["manifest_expected_count_drift"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_demote_gross_manifest_overcount_for_healthy_physical_pages(self):
        flags = _structural_flags(
            doc={"problem_rows": 448, "paper_primary": "clean_pdf_dual"},
            metric={
                "total_boxes": 152,
                "numbered_box_count": 152,
                "unnumbered_box_count": 0,
                "quality_flag_counts": {},
                "pages": [
                    {"box_count": 4, "is_skip_page": False}
                    for _ in range(38)
                ],
            },
            gt_metric=None,
        )

        self.assertEqual(flags, ["manifest_expected_count_drift"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_demote_scan_partial_exam_manifest_overcount(self):
        flags = _structural_flags(
            doc={"problem_rows": 32, "paper_primary": "clean_pdf_dual"},
            metric={
                "total_boxes": 21,
                "numbered_box_count": 21,
                "unnumbered_box_count": 0,
                "quality_flag_counts": {},
                "paper_type_distribution": {
                    "scan_dual": 4,
                    "clean_pdf_dual": 2,
                },
                "pages": [
                    {"box_count": 4, "is_skip_page": False},
                    {"box_count": 5, "is_skip_page": False},
                    {"box_count": 4, "is_skip_page": False},
                    {"box_count": 3, "is_skip_page": False},
                    {"box_count": 3, "is_skip_page": False},
                    {"box_count": 2, "is_skip_page": False},
                ],
            },
            gt_metric=None,
        )

        self.assertEqual(flags, ["manifest_expected_count_drift"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "warn")

    def test_structural_flags_keep_severe_under_when_physical_pages_are_unhealthy(self):
        flags = _structural_flags(
            doc={"problem_rows": 40, "paper_primary": "scanned_pdf"},
            metric={
                "total_boxes": 20,
                "numbered_box_count": 4,
                "unnumbered_box_count": 16,
                "quality_flag_counts": {"all_boxes_unnumbered": 8},
                "pages": [
                    {"box_count": 2, "is_skip_page": False}
                    for _ in range(10)
                ],
            },
            gt_metric=None,
        )

        self.assertIn("severe_under_expected_count", flags)
        self.assertNotIn("manifest_expected_count_drift", flags)
        self.assertEqual(_manifest_quality_grade(flags)["status"], "fail")

    def test_structural_flags_fail_non_question_expected_empty_has_boxes(self):
        flags = _structural_flags(
            doc={"problem_rows": 0, "paper_primary": "answer_key"},
            metric={"total_boxes": 2, "numbered_box_count": 2, "unnumbered_box_count": 0},
            gt_metric=None,
        )

        self.assertEqual(flags, ["non_question_expected_empty_has_boxes"])
        self.assertEqual(_manifest_quality_grade(flags)["status"], "fail")

    def test_extract_predictions_can_use_audit_box_meta(self):
        result = {
            "pages": [
                {
                    "page_index": 0,
                    "image_width": 1000,
                    "image_height": 1000,
                    "boxes": [(0, 0, 1000, 1000)],
                    "numbers": [7],
                    "bbox_meta": [
                        {
                            "display_box": (0, 0, 1000, 1000),
                            "audit_box": (100, 120, 300, 400),
                        }
                    ],
                }
            ]
        }

        display = _extract_predictions(result, box_kind="display")[0]
        audit = _extract_predictions(result, box_kind="audit")[0]

        self.assertEqual(display.bbox.as_tuple(), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(audit.bbox.as_tuple(), (0.1, 0.12, 0.3, 0.4))
        self.assertEqual(audit.number, 7)

    def test_gt_eval_maximizes_match_count_before_iou(self):
        gt_boxes = [
            GroundTruthBox(index=1, number=1, page_index=0, bbox=NormBox(0.0, 0.0, 1.0, 0.70)),
            GroundTruthBox(index=2, number=2, page_index=0, bbox=NormBox(0.0, 0.35, 1.0, 0.65)),
        ]
        pred_boxes = [
            PredictedBox(index=10, number=1, page_index=0, bbox=NormBox(0.0, 0.0, 1.0, 0.70), raw_box=(0, 0, 100, 70)),
            PredictedBox(index=11, number=2, page_index=0, bbox=NormBox(0.0, 0.0, 1.0, 0.40), raw_box=(0, 0, 100, 40)),
        ]

        metrics = evaluate_predictions_against_gt(
            gt_boxes,
            pred_boxes,
            iou_threshold=0.30,
            min_recall=1.0,
            min_precision=0.0,
        )

        self.assertEqual(metrics["matched_count"], 2)
        self.assertEqual(metrics["missed_count"], 0)

    def test_gt_eval_prefers_same_number_when_shared_context_boxes_overlap(self):
        gt_boxes = [
            GroundTruthBox(index=11, number=11, page_index=0, bbox=NormBox(0.50, 0.45, 0.98, 0.69)),
            GroundTruthBox(index=12, number=12, page_index=0, bbox=NormBox(0.50, 0.44, 0.98, 0.92)),
        ]
        pred_boxes = [
            PredictedBox(index=21, number=11, page_index=0, bbox=NormBox(0.51, 0.45, 0.97, 0.69), raw_box=(510, 450, 460, 240)),
            PredictedBox(index=22, number=12, page_index=0, bbox=NormBox(0.51, 0.44, 0.97, 0.92), raw_box=(510, 440, 460, 480)),
        ]

        metrics = evaluate_predictions_against_gt(
            gt_boxes,
            pred_boxes,
            iou_threshold=0.30,
            min_recall=1.0,
            min_precision=0.0,
        )

        pairs = {
            (match["gt_number"], match["pred_number"])
            for match in metrics["matches"]
        }
        self.assertEqual(metrics["matched_count"], 2)
        self.assertIn((11, 11), pairs)
        self.assertIn((12, 12), pairs)

    def test_gt_eval_separates_duplicate_manual_rows_from_physical_misses(self):
        gt_boxes = [
            GroundTruthBox(index=1, number=13, page_index=0, bbox=NormBox(0.10, 0.20, 0.40, 0.30)),
            GroundTruthBox(index=2, number=149, page_index=0, bbox=NormBox(0.105, 0.205, 0.39, 0.29)),
        ]
        pred_boxes = [
            PredictedBox(index=10, number=1, page_index=0, bbox=NormBox(0.10, 0.20, 0.40, 0.30), raw_box=(100, 200, 400, 300)),
        ]

        metrics = evaluate_predictions_against_gt(
            gt_boxes,
            pred_boxes,
            iou_threshold=0.50,
            min_recall=1.0,
            min_precision=0.0,
        )

        self.assertEqual(metrics["missed_count"], 1)
        self.assertEqual(metrics["duplicate_missed_count"], 1)
        self.assertEqual(metrics["physical_gt_count"], 1)
        self.assertEqual(metrics["physical_missed_count"], 0)
        self.assertEqual(metrics["status"], "pass")

    def test_structural_flags_use_physical_missed_count_when_available(self):
        flags = _structural_flags(
            doc={"problem_rows": 2, "paper_primary": "clean_pdf_dual"},
            metric={"total_boxes": 1, "numbered_box_count": 1, "unnumbered_box_count": 0},
            gt_metric={
                "missed_count": 1,
                "physical_missed_count": 0,
                "precision": 1.0,
            },
        )

        self.assertNotIn("manifest_gt_missed", flags)
