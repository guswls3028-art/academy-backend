"""Read-only contract tests for matchup_golden_eval."""
from __future__ import annotations

import inspect
from argparse import ArgumentParser
from pathlib import Path
from unittest import TestCase

from apps.domains.matchup.management.commands import matchup_golden_eval as cmd_module


class CommandContractTests(TestCase):
    def test_command_loadable(self):
        self.assertTrue(callable(cmd_module.Command))

    def test_help_mentions_read_only(self):
        self.assertIn("read-only", cmd_module.Command.help)
        self.assertIn("DB write 0", cmd_module.Command.help)

    def test_arguments(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        actions = {action.dest for action in parser._actions}
        for expected in (
            "files", "input_dir", "output", "source_type", "limit",
            "overlay_limit_pages", "no_overlays", "dispatcher_only",
        ):
            self.assertIn(expected, actions)

    def test_parser_defaults(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args(["--file", "/tmp/a.pdf"])
        self.assertEqual(args.files, ["/tmp/a.pdf"])
        self.assertEqual(args.source_type, "other")
        self.assertEqual(args.overlay_limit_pages, 12)
        self.assertFalse(args.no_overlays)
        self.assertFalse(args.dispatcher_only)


class ReadOnlySourceContractTests(TestCase):
    def test_no_product_write_patterns(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            ".objects.create(",
            ".objects.update(",
            ".objects.update_or_create(",
            ".objects.bulk_create(",
            ".objects.bulk_update(",
            ".objects.get_or_create(",
            ".delete(",
            "selected_problem_ids =",
            "MatchupHitReport.objects",
            "upload_fileobj_to_r2_storage",
            "upload_to_r2",
        ):
            self.assertNotIn(forbidden, src)


class HelperTests(TestCase):
    def test_safe_slug_keeps_ascii_and_hashes(self):
        self.assertRegex(
            cmd_module._safe_slug("sample file.pdf"),
            r"^sample-file.pdf-[0-9a-f]{8}$",
        )
        self.assertRegex(
            cmd_module._safe_slug("매치업테스트"),
            r"^item-[0-9a-f]{8}$",
        )

    def test_page_metrics_count_boxes_and_flags(self):
        metric = cmd_module._page_metrics({
            "page_index": 2,
            "paper_type": "clean_pdf_single",
            "has_embedded_text": True,
            "is_skip_page": False,
            "image_size": [1000, 1000],
            "boxes": [(0, 0, 800, 900), (10, 10, 20, 20)],
            "numbers": [1, None],
        })

        self.assertEqual(metric["page_index"], 2)
        self.assertEqual(metric["box_count"], 2)
        self.assertEqual(metric["numbered_count"], 1)
        self.assertEqual(metric["unnumbered_count"], 1)
        self.assertEqual(metric["large_box_count"], 1)
        self.assertEqual(metric["small_box_count"], 1)
        self.assertEqual(metric["question_marker_count"], 0)
        self.assertIn("page_like_box", metric["quality_flags"])
        self.assertIn("mixed_numbering", metric["quality_flags"])

    def test_page_metrics_count_question_markers_from_text_layer(self):
        metric = cmd_module._page_metrics({
            "page_index": 0,
            "paper_type": "clean_pdf_dual",
            "has_embedded_text": True,
            "is_skip_page": False,
            "image_size": [1000, 1000],
            "boxes": [],
            "numbers": [],
            "page_text": "1. 다음 설명으로 옳은 것은?\n\n문제 2 아래 보기에서 고르시오.",
        })

        self.assertGreater(metric["page_text_len"], 0)
        self.assertEqual(metric["question_marker_count"], 2)
        self.assertIn("no_boxes_non_skip", metric["quality_flags"])

    def test_quality_grade(self):
        self.assertEqual(
            cmd_module._quality_grade({})["status"],
            "pass",
        )
        self.assertEqual(
            cmd_module._quality_grade({"all_boxes_unnumbered": 2})["status"],
            "warn",
        )
        self.assertEqual(
            cmd_module._quality_grade({"no_boxes_non_skip": 1})["status"],
            "fail",
        )
        self.assertEqual(
            cmd_module._quality_grade({}, skipped_for_indexing=True)["status"],
            "skip",
        )

    def test_skip_page_with_boxes_is_flagged(self):
        metric = cmd_module._page_metrics({
            "page_index": 0,
            "paper_type": "non_question",
            "is_skip_page": True,
            "image_size": [100, 100],
            "boxes": [(0, 0, 10, 10)],
            "numbers": [None],
        })

        self.assertIn("skip_page_has_boxes", metric["quality_flags"])
        self.assertIn("non_question_has_boxes", metric["quality_flags"])
        self.assertIn("all_boxes_unnumbered", metric["quality_flags"])

    def test_document_metrics_aggregates_pages(self):
        result = {
            "is_pdf": True,
            "total_boxes": 3,
            "pages": [
                {
                    "page_index": 0,
                    "paper_type": "clean_pdf_single",
                    "has_embedded_text": True,
                    "image_size": [100, 100],
                    "boxes": [(0, 0, 10, 10), (10, 10, 10, 10)],
                    "numbers": [1, 2],
                    "page_text": "1. A\n2. B",
                },
                {
                    "page_index": 1,
                    "paper_type": "non_question",
                    "is_skip_page": True,
                    "image_size": [100, 100],
                    "boxes": [(0, 0, 10, 10)],
                    "numbers": [None],
                },
            ],
        }

        doc = cmd_module._document_metrics(
            Path("fixture.pdf"),
            result,
            source_type="school_exam_pdf",
            overlay_dir=None,
            overlay_limit_pages=0,
        )

        self.assertTrue(doc["ok"])
        self.assertEqual(doc["page_count"], 2)
        self.assertEqual(doc["total_boxes"], 3)
        self.assertEqual(doc["numbered_box_count"], 2)
        self.assertEqual(doc["unnumbered_box_count"], 1)
        self.assertEqual(doc["question_marker_count"], 2)
        self.assertEqual(doc["question_marker_page_count"], 1)
        self.assertEqual(doc["paper_type_distribution"]["clean_pdf_single"], 1)
        self.assertEqual(doc["paper_type_distribution"]["non_question"], 1)
        self.assertEqual(doc["quality_flag_counts"]["non_question_has_boxes"], 1)
        self.assertEqual(doc["quality_grade"]["status"], "fail")

    def test_skipped_document_metrics(self):
        doc = cmd_module._skipped_document_metrics(
            Path("answer.pdf"),
            source_type="answer_key",
        )

        self.assertTrue(doc["ok"])
        self.assertTrue(doc["skipped_for_indexing"])
        self.assertEqual(doc["page_count"], 0)
        self.assertEqual(doc["total_boxes"], 0)
        self.assertEqual(doc["paper_type_distribution"], {"answer_key": 1})
        self.assertEqual(doc["quality_grade"]["status"], "skip")
