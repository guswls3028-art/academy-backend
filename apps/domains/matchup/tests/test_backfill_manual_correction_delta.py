"""Stage 6.5-backfill (2026-05-08) — matchup_backfill_manual_correction_delta tests.

검증:
- command class loadable / arguments
- tenant-id required (CommandError)
- dry-run default
- limit 검증 (0 < limit <= 5000)
- selected_problem_ids / hit_report / callback 미접근 (정적 검사)
- R2 write / OCR/VLM SDK import 0 (정적 검사)
- 6.5 hook (`_record_manual_correction_delta`) 재사용 — 직접 helper 호출

ORM mock 기반 — DB 무관.
"""
from __future__ import annotations

import inspect
from argparse import ArgumentParser
from unittest import TestCase

from django.core.management.base import CommandError

from apps.domains.matchup.management.commands import (
    matchup_backfill_manual_correction_delta as cmd_module,
)


class CommandLoadableTests(TestCase):
    def test_class_loadable(self):
        self.assertTrue(callable(cmd_module.Command))

    def test_help_mentions_stage_and_dry_run(self):
        h = cmd_module.Command.help
        self.assertIn("6.5-backfill", h)
        self.assertIn("dry-run", h.lower())
        self.assertIn("tenant", h.lower())

    def test_arguments_defined(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        actions = {a.dest for a in parser._actions}
        for required in ("tenant_id", "limit", "dry_run", "sample"):
            self.assertIn(required, actions, f"missing argument: {required}")

    def test_tenant_id_required(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
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


class CommandValidationTests(TestCase):
    def test_invalid_tenant_id_raises(self):
        c = cmd_module.Command()
        for bad in (0, -1, -100):
            with self.assertRaises(CommandError):
                c.handle(tenant_id=bad, limit=10, dry_run=True, sample=5)

    def test_invalid_limit_raises(self):
        c = cmd_module.Command()
        for bad in (0, -1, 5001, 100000):
            with self.assertRaises(CommandError):
                c.handle(tenant_id=2, limit=bad, dry_run=True, sample=5)

    def test_negative_sample_raises(self):
        c = cmd_module.Command()
        with self.assertRaises(CommandError):
            c.handle(tenant_id=2, limit=10, dry_run=True, sample=-1)


class CommandSafetyRegressionTests(TestCase):
    def test_no_callback_or_dispatcher_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "_handle_matchup_ai_result", "_handle_matchup_index_result",
            "_handle_matchup_manual_result", "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",
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
            self.assertNotIn(forbidden, src,
                             f"backfill 안에서 R2/S3 write 패턴 '{forbidden}' 발견")

    def test_no_ocr_vlm_sdk_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "import google.generativeai", "from google.generativeai",
            "import openai", "from openai",
            "import pytesseract", "from pytesseract",
            "import anthropic", "from anthropic",
        ):
            self.assertNotIn(forbidden, src,
                             f"backfill 안에서 OCR/VLM SDK '{forbidden}' 발견")

    def test_no_selected_or_hit_report_access(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            ".selected_problem_ids", "selected_problem_ids =",
            "MatchupHitReport.objects", "MatchupHitReportEntry.objects",
            "MatchupHitReport(", "MatchupHitReportEntry(",
        ):
            self.assertNotIn(forbidden, src,
                             f"backfill 안에서 hit_report/selected 접근 '{forbidden}' 발견")

    def test_no_matchup_problem_modify(self):
        """기존 MatchupProblem 수정 X — read 만."""
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "MatchupProblem.objects.update(", ".update_or_create(",
            "p.save(", "p.delete(", "MatchupProblem.objects.create(",
            "MatchupProblem.objects.filter(...).update",
        ):
            self.assertNotIn(forbidden, src,
                             f"backfill 안에서 MatchupProblem 수정 '{forbidden}' 발견")

    def test_tenant_filter_required(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("tenant_id=tenant_id", src)
        self.assertIn("required=True", src)


class IdempotencyContractTests(TestCase):
    """idempotent backfill — 같은 problem 재실행 시 row 누적 X 정적 검증."""

    def test_already_backfilled_lookup_present(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("already_backfilled", src)
        self.assertIn("correction_type=CORRECTION_TYPE", src)

    def test_correction_type_constant_manual_create(self):
        self.assertEqual(cmd_module.CORRECTION_TYPE, "manual_create")

    def test_skip_logic_uses_problem_id_in_set(self):
        src = inspect.getsource(cmd_module)
        # `if p.id in already_backfilled: skip_already += 1; continue`
        self.assertIn("p.id in already_backfilled", src)


class ReuseSixFiveHookContractTests(TestCase):
    """Stage 6.5 hook 재사용 — 직접 _record_manual_correction_delta 호출."""

    def test_helper_imported(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("from apps.domains.matchup.services import _record_manual_correction_delta", src)

    def test_helper_called_with_actor_none(self):
        src = inspect.getsource(cmd_module)
        # backfill 은 이전 데이터 actor 모름 → None
        self.assertIn("actor=None", src)
        self.assertIn("is_recreate=False", src)


class CommandLogicTests(TestCase):
    def test_default_limit_in_range(self):
        self.assertGreater(cmd_module.DEFAULT_LIMIT, 0)
        self.assertLessEqual(cmd_module.DEFAULT_LIMIT, 5000)

    def test_default_sample_reasonable(self):
        self.assertGreaterEqual(cmd_module.DEFAULT_SAMPLE, 0)
        self.assertLessEqual(cmd_module.DEFAULT_SAMPLE, 50)
