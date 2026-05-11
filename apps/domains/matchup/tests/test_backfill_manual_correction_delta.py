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


class SkipClassificationTests(TestCase):
    """skip_merged 분류 정적 검증 — meta.merged=True + bbox 없음 → skip_merged.

    command source 의 분류 로직을 정적 검사 + integration smoke 로 검증.
    """

    def test_skip_merged_category_exists_in_source(self):
        src = inspect.getsource(cmd_module)
        # skip_merged 카운터 정의 + 분류 분기 + sample 보존
        self.assertIn("skip_merged", src)
        self.assertIn('meta.get("merged") is True', src)
        self.assertIn("merged_samples", src)

    def test_skip_merged_sample_records_audit_fields(self):
        """sample dict 가 problem_id / doc_id / number / merged_from /
        merged_numbers / image_key 을 audit trail 로 보존."""
        src = inspect.getsource(cmd_module)
        # sample 구성 키 확인
        for required_key in (
            '"problem_id"', '"doc_id"', '"number"',
            '"merged_from"', '"merged_numbers"', '"image_key"',
        ):
            self.assertIn(required_key, src,
                          f"merged_samples 에 {required_key} 보존 안 됨")

    def test_general_no_bbox_still_classified_as_skip_no_bbox(self):
        """merge 가 아닌 일반 manual=True + bbox 없음 — skip_no_bbox 유지."""
        src = inspect.getsource(cmd_module)
        # merge 가 False 인 경우 skip_no_bbox 분기 유지
        self.assertIn("skip_no_bbox += 1", src)
        # bbox_invalid + meta.merged != True path 가 skip_no_bbox 로 떨어지는지
        # 간접 검증 — 현재 구현이 "if meta.get('merged') is True: skip_merged ...
        # continue / skip_no_bbox += 1" 순서. order 검증.
        merged_idx = src.find('meta.get("merged") is True')
        no_bbox_first = src.find("skip_no_bbox += 1", merged_idx)
        self.assertGreater(no_bbox_first, merged_idx,
                           "skip_no_bbox 분기가 skip_merged 분기 뒤에 와야 함")

    def test_skip_merged_does_not_trigger_actual_insert(self):
        """skip_merged path 는 candidates 에 추가 X — actual INSERT 대상 아님.

        merged 분기 (`if meta.get("merged") is True:` ... `continue`) 안에
        candidates.append 가 없어야 함.
        """
        src = inspect.getsource(cmd_module)
        merged_block_start = src.find('if meta.get("merged") is True:')
        # merged 분기는 첫 `continue` 로 끝남 (분기 내부에 한 번)
        first_continue = src.find("continue", merged_block_start)
        self.assertGreater(first_continue, merged_block_start,
                           "merged 분기 안에 continue 가 있어야")
        merged_block = src[merged_block_start:first_continue + len("continue")]
        self.assertNotIn("candidates.append", merged_block,
                         "skip_merged 분기 안에서 candidates.append 가 있으면 안 됨")
        # candidates.append 는 dedup/insert path 의 마지막에만 1회 (skip 분기 외부)
        self.assertEqual(
            src.count("candidates.append"), 1,
            "candidates.append 는 정확히 1곳 (정상 path 끝) 에서만 호출",
        )

    def test_skip_merged_summary_in_stdout_writer(self):
        """summary 출력에 skip_merged 항목 포함 (운영자 가시성)."""
        src = inspect.getsource(cmd_module)
        self.assertIn("skip_merged={skip_merged}", src)
