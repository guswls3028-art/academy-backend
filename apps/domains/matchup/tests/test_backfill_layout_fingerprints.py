"""Stage 6.6.5 (2026-05-08) — matchup_backfill_layout_fingerprints command tests.

검증:
- command class loadable / arguments
- tenant-id required (CommandError 시 raise)
- dry-run default
- limit 범위 검증 (0 < limit <= 500)
- selected_problem_ids / hit_report / callback 미접근 (정적 import 검사)

ORM mock 기반 — DB 무관. 실 호출 path 는 통합 smoke 에서 별도 검증 (운영 deploy 후).
"""
from __future__ import annotations

import ast
import inspect
from argparse import ArgumentParser
from unittest import TestCase

from django.core.management.base import CommandError

from apps.domains.matchup.management.commands import (
    matchup_backfill_layout_fingerprints as cmd_module,
)


class CommandLoadableTests(TestCase):
    def test_class_loadable(self):
        self.assertTrue(callable(cmd_module.Command))

    def test_help_mentions_stage_and_dry_run(self):
        self.assertIn("6.6.5", cmd_module.Command.help)
        self.assertIn("dry-run", cmd_module.Command.help.lower())

    def test_arguments_defined(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        actions = {a.dest for a in parser._actions}
        for required in ("tenant_id", "doc_id", "limit", "dry_run"):
            self.assertIn(required, actions, f"missing argument: {required}")

    def test_tenant_id_required(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        # tenant-id required → exit 시 SystemExit
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_dry_run_default_true(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args(["--tenant-id", "2"])
        self.assertTrue(args.dry_run)

    def test_no_dry_run_flag_disables(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args(["--tenant-id", "2", "--no-dry-run"])
        self.assertFalse(args.dry_run)

    def test_limit_default(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args(["--tenant-id", "2"])
        self.assertEqual(args.limit, cmd_module.DEFAULT_LIMIT)


class CommandValidationTests(TestCase):
    """handle() 의 입력 검증 — invalid tenant_id / limit 시 CommandError."""

    def test_invalid_tenant_id_raises(self):
        c = cmd_module.Command()
        for bad in (0, -1, -100):
            with self.assertRaises(CommandError):
                c.handle(
                    tenant_id=bad, doc_id=None, limit=10, dry_run=True,
                )

    def test_invalid_limit_raises(self):
        c = cmd_module.Command()
        for bad in (0, -1, 501, 10000):
            with self.assertRaises(CommandError):
                c.handle(
                    tenant_id=2, doc_id=None, limit=bad, dry_run=True,
                )


class CommandSafetyRegressionTests(TestCase):
    def test_no_callback_or_dispatcher_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "_handle_matchup_ai_result", "_handle_matchup_index_result",
            "_handle_matchup_manual_result", "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",
            "MatchupHitReport.objects", "MatchupHitReportEntry.objects",
        ):
            self.assertNotIn(
                forbidden, src,
                f"backfill command 안에서 forbidden token '{forbidden}' 발견",
            )

    def test_no_r2_write_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "upload_fileobj_to_r2_storage", "upload_to_r2",
            "r2.put_object", "boto3.client", "S3.put_object",
        ):
            self.assertNotIn(
                forbidden, src,
                f"backfill command 안에서 R2/S3 write 패턴 '{forbidden}' 발견",
            )

    def test_no_selected_problem_ids_access(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            ".selected_problem_ids", "selected_problem_ids =",
            "MatchupHitReportEntry",
        ):
            self.assertNotIn(
                forbidden, src,
                f"backfill command 안에서 selected/hit_report 접근 '{forbidden}' 발견",
            )

    def test_tenant_filter_present(self):
        """tenant filter 보장 — cross-tenant 영구 차단."""
        src = inspect.getsource(cmd_module)
        # tenant_id 가 query filter 에 명시 사용
        self.assertIn("tenant_id=tenant_id", src)
        self.assertIn("required=True", src)


class CommandLogicTests(TestCase):
    """default values / constants 검증."""

    def test_default_fingerprint_version(self):
        self.assertEqual(cmd_module.DEFAULT_FINGERPRINT_VERSION, 1)

    def test_default_limit_in_range(self):
        self.assertGreater(cmd_module.DEFAULT_LIMIT, 0)
        self.assertLessEqual(cmd_module.DEFAULT_LIMIT, 500)


class ExtractPdfFirstPageMetricsContractTests(TestCase):
    """`_extract_pdf_first_page_metrics` 의 정적 contract — 실 PDF download 미수행."""

    def test_helper_exists_with_correct_signature(self):
        from apps.domains.matchup.services import _extract_pdf_first_page_metrics
        sig = inspect.signature(_extract_pdf_first_page_metrics)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["document"])

    def test_helper_no_callback_or_r2_write(self):
        from apps.domains.matchup.services import _extract_pdf_first_page_metrics
        src = inspect.getsource(_extract_pdf_first_page_metrics)
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "upload_fileobj_to_r2_storage",
            ".selected_problem_ids",
        ):
            self.assertNotIn(forbidden, src,
                             f"_extract_pdf_first_page_metrics 안에서 '{forbidden}' 발견")
