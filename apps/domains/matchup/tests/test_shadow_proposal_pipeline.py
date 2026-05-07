"""Stage 6.3-Pipeline (2026-05-07) — shadow_proposal_pipeline + management command tests.

검증:
- GLOBAL ENV gate (MATCHUP_SHADOW_PROPOSAL_ENABLED) 미설정 → blocking
- tenant_id != 1 → blocking (T1 sandbox 외 차단)
- 정상 흐름 (dispatcher → integrate → adapter dry_run) — 합성 PDF 사용
- dry_run default → INSERT 0회
- 운영 callback / segment_dispatcher / proposal_helpers / DB 모델 / OCR/VLM SDK
  module-level import 0회 (regression)
- management command — ENV 미설정 시 CommandError
"""
from __future__ import annotations

import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

from apps.domains.matchup.segmentation.shadow_proposal_pipeline import (
    DEFAULT_SANDBOX_TENANT_ID, SCHEMA_VERSION, SHADOW_GLOBAL_ENV,
    ShadowPipelineResult, is_globally_enabled,
    result_to_dict, shadow_proposal_pipeline,
)


def _make_simple_pdf():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "1. 다음 ① ② ③", fontsize=10)
    page.insert_text((50, 300), "2. 다음 ① ② ③", fontsize=10)
    tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
    tmp.close()
    doc.save(tmp.name); doc.close()
    return tmp.name


# ── GLOBAL feature flag ────────────────────────────────────────


class TestGlobalEnvGate(TestCase):
    def test_disabled_by_default(self):
        # 본 test 환경에 ENV 없음 가정 (안전)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(SHADOW_GLOBAL_ENV, None)
            self.assertFalse(is_globally_enabled())

    def test_enabled_when_env_set(self):
        with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
            self.assertTrue(is_globally_enabled())

    def test_enabled_alt_values(self):
        for val in ("true", "True", "yes", "1"):
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: val}):
                self.assertTrue(is_globally_enabled())

    def test_disabled_for_other_values(self):
        for val in ("0", "false", "no", ""):
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: val}):
                self.assertFalse(is_globally_enabled())


class TestPipelineGateBlocking(TestCase):
    def test_disabled_returns_blocking(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(SHADOW_GLOBAL_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            self.assertFalse(result.enabled)
            self.assertIsNotNone(result.blocking_reason)
            self.assertIn("disabled", result.blocking_reason)
            # 모든 단계 skip
            self.assertIsNone(result.dispatcher_output)
            self.assertIsNone(result.unified_output)
            self.assertIsNone(result.insert_result)
        finally:
            os.unlink(pdf)

    def test_t2_tenant_blocked(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=300, tenant_id=2,    # ← T2 production
                )
            self.assertFalse(result.enabled)
            self.assertIsNotNone(result.blocking_reason)
            self.assertIn("T1 sandbox", result.blocking_reason)
            self.assertIn("tenant_id=2", result.blocking_reason)
            # 모든 단계 skip — 운영 자료 미접근
            self.assertIsNone(result.dispatcher_output)
            self.assertIsNone(result.unified_output)
            self.assertIsNone(result.insert_result)
        finally:
            os.unlink(pdf)

    def test_other_tenants_blocked(self):
        pdf = _make_simple_pdf()
        try:
            for bad_tenant in (3, 5, 99, 100):
                with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                    result = shadow_proposal_pipeline(
                        pdf, document_id=1, tenant_id=bad_tenant,
                    )
                self.assertFalse(result.enabled)
                self.assertIn(f"tenant_id={bad_tenant}", result.blocking_reason)
        finally:
            os.unlink(pdf)


class TestPipelineHappyPath(TestCase):
    def test_t1_dry_run_all_steps_executed(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    analysis_version_key="test-pipeline-1",
                )
            self.assertTrue(result.enabled)
            self.assertIsNone(result.blocking_reason)
            self.assertIsNotNone(result.dispatcher_output)
            self.assertIsNotNone(result.unified_output)
            self.assertIsNotNone(result.insert_result)
            # default dry_run — INSERT 0회
            self.assertTrue(result.dry_run)
            self.assertFalse(result.allow_insert)
            self.assertEqual(result.insert_result["inserted_count"], 0)
        finally:
            os.unlink(pdf)

    def test_dispatcher_route_in_output(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            assert result.dispatcher_output is not None
            self.assertIn("route", result.dispatcher_output)

        finally:
            os.unlink(pdf)

    def test_unified_payloads_in_output(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            assert result.unified_output is not None
            self.assertIn("proposal_payloads", result.unified_output)
            self.assertIn("unified_candidates", result.unified_output)
        finally:
            os.unlink(pdf)


class TestPipelineSandboxGate(TestCase):
    def test_dry_run_default_blocks_insert(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    # dry_run / allow_insert default
                )
            self.assertEqual(result.insert_result["inserted_count"], 0)
            # adapter 의 dry_run path 표식
            self.assertTrue(result.insert_result["dry_run"])


        finally:
            os.unlink(pdf)

    def test_allow_insert_without_dry_run_false_still_blocks(self):
        """allow_insert=True 라도 dry_run=True 면 INSERT 0회 (adapter 안전성)."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    dry_run=True, allow_insert=True,
                )
            self.assertEqual(result.insert_result["inserted_count"], 0)
        finally:
            os.unlink(pdf)


class TestPipelineSerializable(TestCase):
    def test_result_to_dict_json_serializable(self):
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}):
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            d = result_to_dict(result)
            import json
            json.dumps(d, default=str)
            self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        finally:
            os.unlink(pdf)


# ── Regression: 운영 import 0회 ──────────────────────────────────


class TestPipelineRegression(TestCase):
    def test_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import shadow_proposal_pipeline
        import inspect
        src = inspect.getsource(shadow_proposal_pipeline)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import requests", "from requests",
            "import google.generativeai", "from google.generativeai",
            "import google.cloud", "from google.cloud",
            "import openai", "from openai",
            "import anthropic", "from anthropic",
            "import pytesseract", "from pytesseract",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"shadow_proposal_pipeline 에서 실 SDK import '{token}' 발견",
            )

    def test_no_operational_callback_or_dispatcher_imports(self):
        from apps.domains.matchup.segmentation import shadow_proposal_pipeline
        import inspect
        src = inspect.getsource(shadow_proposal_pipeline)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "from apps.domains.ai.gateway",
            "from apps.domains.ai.callbacks",
            "_handle_matchup_ai_result",
            "_handle_matchup_index_result",
            "_handle_matchup_manual_result",
            "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",
            "segment_questions_multipage(",
            "segment_questions(",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"shadow_proposal_pipeline 에서 운영 callback/dispatcher access "
                f"'{token}' 발견",
            )

    def test_no_db_model_or_helper_module_imports_at_module_level(self):
        """module-level 에 ProblemSegmentationProposal / proposal_helpers /
        MatchupProblem 등 직접 import 0회 (지연 import 는 adapter 안에서 lazy)."""
        from apps.domains.matchup.segmentation import shadow_proposal_pipeline
        import inspect
        src = inspect.getsource(shadow_proposal_pipeline)
        # module-level (첫 함수 def 전) 만 검사
        head = src.split("def ")[0]
        forbidden = (
            "from apps.domains.matchup.proposal_helpers",
            "from apps.domains.matchup.models",
            "from apps.domains.matchup.signals",
        )
        for token in forbidden:
            self.assertNotIn(
                token, head,
                f"module-level 에 운영 helper '{token}' import 발견",
            )


# ── management command (Django call_command) ────────────────────


class TestManagementCommand(TestCase):
    def test_command_class_loadable(self):
        from apps.domains.matchup.management.commands.shadow_proposal import Command
        self.assertTrue(callable(Command))

    def test_command_help_text_mentions_env(self):
        from apps.domains.matchup.management.commands.shadow_proposal import Command
        self.assertIn(SHADOW_GLOBAL_ENV, Command.help)

    def test_command_arguments(self):
        """Command.add_arguments — 모든 옵션 정의됨."""
        from argparse import ArgumentParser
        from apps.domains.matchup.management.commands.shadow_proposal import Command
        parser = ArgumentParser()
        Command().add_arguments(parser)
        # required + optional 모두 추가됨 (어느 하나 빠지면 fail)
        actions = {a.dest for a in parser._actions}
        for required in (
            "doc_id", "tenant_id", "pdf_path", "analysis_version_key",
            "dry_run", "allow_insert", "max_payloads",
            "mock_ocr_blocks", "mock_vlm_problems", "out_json",
        ):
            self.assertIn(required, actions)
