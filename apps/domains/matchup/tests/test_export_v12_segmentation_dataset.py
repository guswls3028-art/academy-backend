"""Stage P1 V12 export — read-only command 정적 검증.

검증:
- command 옵션 / tenant required / dry-run default
- DB write 0 (정적 검사)
- Cross-tenant filter 보장
- doc-level split leakage 정책 코드 존재
- excluded paper_type / skip_merged 정책
- ORM 호출 chain 이 select_related + iterator 만 (mass scan 안전)

ORM mock 미사용 — argparse / source 정적 검사 만. 실 데이터 검증은 운영 dry-run.
"""
from __future__ import annotations

import inspect
from argparse import ArgumentParser
from unittest import TestCase

from django.core.management.base import CommandError

from apps.domains.matchup.management.commands import (
    matchup_export_v12_segmentation_dataset as cmd_module,
)


class CommandLoadableTests(TestCase):
    def test_class_loadable(self):
        self.assertTrue(callable(cmd_module.Command))

    def test_help_mentions_read_only(self):
        h = cmd_module.Command.help
        self.assertIn("read-only", h)
        self.assertIn("DB write 0", h)

    def test_required_args(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        actions = {a.dest for a in parser._actions}
        for required in ("tenant_id", "output", "dry_run", "seed", "sample"):
            self.assertIn(required, actions)

    def test_tenant_id_required(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--output", "/tmp/x.jsonl"])

    def test_output_required(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--tenant-id", "2"])

    def test_dry_run_default_true(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args(["--tenant-id", "2", "--output", "/tmp/x.jsonl"])
        self.assertTrue(args.dry_run)

    def test_no_dry_run_flag(self):
        parser = ArgumentParser()
        cmd_module.Command().add_arguments(parser)
        args = parser.parse_args([
            "--tenant-id", "2", "--output", "/tmp/x.jsonl", "--no-dry-run",
        ])
        self.assertFalse(args.dry_run)


class CommandValidationTests(TestCase):
    def test_invalid_tenant_id_raises(self):
        c = cmd_module.Command()
        for bad in (0, -1, -100):
            with self.assertRaises(CommandError):
                c.handle(
                    tenant_id=bad, output="/tmp/x.jsonl",
                    dry_run=True, seed=None, sample=10,
                )

    def test_empty_output_raises(self):
        c = cmd_module.Command()
        with self.assertRaises(CommandError):
            c.handle(
                tenant_id=2, output="",
                dry_run=True, seed=None, sample=10,
            )

    def test_negative_sample_raises(self):
        c = cmd_module.Command()
        with self.assertRaises(CommandError):
            c.handle(
                tenant_id=2, output="/tmp/x.jsonl",
                dry_run=True, seed=None, sample=-1,
            )


def _module_source_without_docstring(module) -> str:
    """모듈 source 에서 module-level docstring 제거 (정책 설명 단어 → 정적 검사 false-positive 차단)."""
    import ast
    src = inspect.getsource(module)
    try:
        tree = ast.parse(src)
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            tree.body = tree.body[1:]
        return ast.unparse(tree)
    except Exception:
        return src


class ReadOnlyContractTests(TestCase):
    """source 정적 검사 — DB write / R2 write / OCR/VLM 호출 0.

    docstring 은 검사 제외 (정책 설명 단어 → false positive).
    """

    def test_no_db_write_calls(self):
        body = _module_source_without_docstring(cmd_module)
        for forbidden in (
            ".save(", ".delete(",
            ".objects.create(", ".objects.update(",
            ".objects.update_or_create(", ".objects.bulk_create(",
            ".objects.bulk_update(", ".objects.get_or_create(",
        ):
            self.assertNotIn(
                forbidden, body,
                f"V12 export 안에서 DB write 패턴 '{forbidden}' 발견 (docstring 제외)",
            )

    def test_no_r2_write_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "upload_fileobj_to_r2_storage", "upload_to_r2",
            "r2.put_object", "boto3.client", "S3.put_object",
        ):
            self.assertNotIn(forbidden, src, f"R2/S3 write 패턴 '{forbidden}' 발견")

    def test_no_ocr_vlm_sdk_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "import google.generativeai", "from google.generativeai",
            "import openai", "from openai",
            "import pytesseract", "from pytesseract",
            "import anthropic", "from anthropic",
        ):
            self.assertNotIn(forbidden, src, f"OCR/VLM SDK '{forbidden}' 발견")

    def test_no_callback_or_dispatcher_imports(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "_handle_matchup_ai_result", "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",
        ):
            self.assertNotIn(forbidden, src, f"callback/dispatcher '{forbidden}' 발견")

    def test_no_selected_or_hit_report_modify(self):
        src = inspect.getsource(cmd_module)
        for forbidden in (
            ".selected_problem_ids =",
            "MatchupHitReport.objects.create(",
            "MatchupHitReportEntry.objects.create(",
        ):
            self.assertNotIn(forbidden, src,
                             f"selected/hit_report 수정 '{forbidden}' 발견")

    def test_tenant_filter_required(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("tenant_id=tenant_id", src)
        self.assertIn("required=True", src)


class SplitPolicyTests(TestCase):
    def test_train_ratio_sum(self):
        s = sum(cmd_module.SPLIT_RATIOS.values())
        self.assertAlmostEqual(s, 1.0, places=4)

    def test_eval_subset_paper_types(self):
        # student_answer_photo / scan_* 는 eval subset 분리
        self.assertIn("student_answer_photo", cmd_module.EVAL_PHOTO_TYPES)
        self.assertIn("scan_single", cmd_module.EVAL_SCAN_TYPES)
        self.assertIn("scan_dual", cmd_module.EVAL_SCAN_TYPES)

    def test_excluded_paper_types(self):
        for excluded in ("non_question", "side_notes", "unknown",
                          "explanation", "answer_key"):
            self.assertIn(excluded, cmd_module.EXCLUDED_PAPER_TYPES)

    def test_train_types_clean_pdf(self):
        self.assertIn("clean_pdf_dual", cmd_module.TRAIN_TYPES)
        self.assertIn("clean_pdf_single", cmd_module.TRAIN_TYPES)

    def test_correction_type_manual_create_only(self):
        self.assertEqual(cmd_module.CORRECTION_TYPE, "manual_create")

    def test_doc_level_split_logic_present(self):
        """doc-level split leakage 방지 — paper_to_docs / doc_split 매핑 존재."""
        src = inspect.getsource(cmd_module)
        self.assertIn("doc_split", src)
        self.assertIn("paper_to_docs", src)
        self.assertIn("leak_docs", src)
        self.assertIn("leakage_check", src)

    def test_deterministic_seed(self):
        """기본 seed = 42 + tenant_id (재실행 동일 split 보장)."""
        src = inspect.getsource(cmd_module)
        self.assertIn("seed = 42 + tenant_id", src)


class DataIntegrityContractTests(TestCase):
    def test_skip_indexable_false(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("skip_indexable_false", src)
        self.assertIn('doc_meta.get("indexable") is False', src)

    def test_skip_no_bbox_and_page(self):
        src = inspect.getsource(cmd_module)
        self.assertIn("skip_no_bbox", src)
        self.assertIn("skip_no_page", src)

    def test_select_related_used(self):
        """ORM N+1 회피 — select_related 사용."""
        src = inspect.getsource(cmd_module)
        self.assertIn("select_related", src)

    def test_iterator_chunk_size(self):
        """대용량 read 시 chunk_size 명시 — 메모리 안전."""
        src = inspect.getsource(cmd_module)
        self.assertIn("iterator(chunk_size=", src)


class JSONLOutputContractTests(TestCase):
    def test_jsonl_format_per_row(self):
        """JSONL — row 마다 1줄 + json dumps."""
        src = inspect.getsource(cmd_module)
        # write logic 안에 json.dumps + newline pattern
        self.assertIn("json.dumps", src)
        self.assertIn('f.write("\\n")', src)

    def test_jsonl_ascii_safe(self):
        """ensure_ascii=False — 한글/utf-8 보존."""
        src = inspect.getsource(cmd_module)
        self.assertIn("ensure_ascii=False", src)
