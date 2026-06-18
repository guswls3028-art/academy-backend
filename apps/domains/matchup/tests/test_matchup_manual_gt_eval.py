from django.test import SimpleTestCase

from apps.domains.matchup.management.commands.matchup_manual_gt_eval import (
    GroundTruthBox,
    NormBox,
    PredictedBox,
    _box_iou,
    evaluate_predictions_against_gt,
)


class ManualGtEvalHelperTests(SimpleTestCase):
    def test_box_iou_exact_match(self):
        box = NormBox(0.1, 0.2, 0.3, 0.4)
        self.assertEqual(_box_iou(box, box), 1.0)

    def test_evaluate_matches_by_page_and_counts_extra(self):
        gt = [
            GroundTruthBox(index=0, number=1, page_index=0, bbox=NormBox(0.1, 0.1, 0.2, 0.2)),
            GroundTruthBox(index=1, number=2, page_index=1, bbox=NormBox(0.5, 0.5, 0.2, 0.2)),
        ]
        pred = [
            PredictedBox(
                index=0,
                number=1,
                page_index=0,
                bbox=NormBox(0.1, 0.1, 0.2, 0.2),
                raw_box=(10, 10, 20, 20),
            ),
            PredictedBox(
                index=1,
                number=99,
                page_index=1,
                bbox=NormBox(0.0, 0.0, 0.1, 0.1),
                raw_box=(0, 0, 10, 10),
            ),
        ]

        result = evaluate_predictions_against_gt(gt, pred, iou_threshold=0.5)

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["missed_count"], 1)
        self.assertEqual(result["extra_count"], 1)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["status"], "fail")

    def test_evaluate_passes_only_when_recall_and_precision_thresholds_hold(self):
        gt = [
            GroundTruthBox(index=0, number=1, page_index=0, bbox=NormBox(0.1, 0.1, 0.2, 0.2)),
        ]
        pred = [
            PredictedBox(
                index=0,
                number=1,
                page_index=0,
                bbox=NormBox(0.1, 0.1, 0.2, 0.2),
                raw_box=(10, 10, 20, 20),
            ),
        ]

        result = evaluate_predictions_against_gt(
            gt,
            pred,
            iou_threshold=0.5,
            min_recall=1.0,
            min_precision=1.0,
        )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["number_match_count"], 1)
        self.assertEqual(result["number_match_ratio"], 1.0)
