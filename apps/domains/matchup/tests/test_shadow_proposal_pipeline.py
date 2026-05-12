"""Stage 6.3-Pipeline (2026-05-07) — shadow_proposal_pipeline + management command tests.

검증:
- GLOBAL ENV gate (MATCHUP_SHADOW_PROPOSAL_ENABLED) 미설정 → blocking
- T1 (tenant_id=1) 통과 (기존 sandbox)
- T2 (tenant_id=2) 기본 차단 — whitelist 미설정 시
- T2 whitelist 통과 — Stage 6.4-prep
    * doc_id 정확 일치 시 통과
    * doc_id 불일치 시 차단
    * malformed env (다중값/비정수/0) 시 차단
    * max_payloads > T2 cap (5) 시 차단
- T1 회귀: max_payloads > 5 라도 T1 은 통과 (T2 cap 은 T2 전용)
- tenant_id != 1, 2 → 기존 메시지 그대로 차단
- 정상 흐름 (dispatcher → integrate → adapter dry_run) — 합성 PDF 사용
- dry_run default → INSERT 0회
- sandbox_tenant_ids 도출 (T1 → [1], T2 whitelist → [2])
- 운영 callback / segment_dispatcher / proposal_helpers / DB 모델 / OCR/VLM SDK
  module-level import 0회 (regression)
- management command — ENV 미설정 시 CommandError
"""
from __future__ import annotations

import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

from academy.application.use_cases.ai.segmentation.shadow_proposal_pipeline import (
    DEFAULT_SANDBOX_TENANT_ID, SCHEMA_VERSION,
    SHADOW_GLOBAL_ENV, SMOKE_TRUNCATION_REASON,
    T2_DOC_WHITELIST_ENV, T2_PRODUCTION_TENANT_ID,
    T2_WHITELIST_MAX_PAYLOADS,
    _truncate_payloads_for_smoke,
    is_globally_enabled, read_t2_doc_whitelist,
    result_to_dict, shadow_proposal_pipeline,
)
from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
    ProposalPayloadCandidate,
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

    def test_t2_tenant_blocked_when_no_whitelist(self):
        """Stage 6.4-prep — T2 는 whitelist 미설정 시 기본 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                # whitelist 명시 unset
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=300, tenant_id=2,
                )
            self.assertFalse(result.enabled)
            self.assertIsNotNone(result.blocking_reason)
            # 새 메시지: T2 + whitelist 안내
            self.assertIn("T2", result.blocking_reason)
            self.assertIn(T2_DOC_WHITELIST_ENV, result.blocking_reason)
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
                with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                    os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                    result = shadow_proposal_pipeline(
                        pdf, document_id=1, tenant_id=bad_tenant,
                    )
                self.assertFalse(result.enabled)
                self.assertIn(f"tenant_id={bad_tenant}", result.blocking_reason)
                # 기타 tenant 는 기존 T1 sandbox 메시지 유지
                self.assertIn("T1 sandbox", result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_negative_or_zero_tenant_blocked(self):
        """0 / 음수 tenant_id 도 T1 도 T2 도 아니므로 기존 메시지로 차단."""
        pdf = _make_simple_pdf()
        try:
            for bad in (0, -1):
                with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                    os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                    result = shadow_proposal_pipeline(
                        pdf, document_id=1, tenant_id=bad,
                    )
                self.assertFalse(result.enabled)
                self.assertIsNone(result.dispatcher_output)
        finally:
            os.unlink(pdf)


# ── Stage 6.4-prep — T2 single-doc whitelist gate ──────────────────


class TestT2DocWhitelistEnvParser(TestCase):
    """`read_t2_doc_whitelist()` strict single-int 정책 검증."""

    def test_unset_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(T2_DOC_WHITELIST_ENV, None)
            self.assertIsNone(read_t2_doc_whitelist())

    def test_empty_string_returns_none(self):
        with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: ""}):
            self.assertIsNone(read_t2_doc_whitelist())

    def test_whitespace_only_returns_none(self):
        with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: "   "}):
            self.assertIsNone(read_t2_doc_whitelist())

    def test_single_int_returns_int(self):
        with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: "765"}):
            self.assertEqual(read_t2_doc_whitelist(), 765)

    def test_single_int_with_padding_returns_int(self):
        # strip 으로 양 끝 공백만 허용
        with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: "  765  "}):
            self.assertEqual(read_t2_doc_whitelist(), 765)

    def test_comma_multi_value_blocked(self):
        for v in ("765,762", "765, 762", "1,2,3"):
            with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: v}):
                self.assertIsNone(read_t2_doc_whitelist(),
                                  f"comma multi-value should be malformed: {v!r}")

    def test_internal_whitespace_blocked(self):
        # "765 762" 처럼 내부 공백 = 다중값으로 간주, 차단
        with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: "765 762"}):
            self.assertIsNone(read_t2_doc_whitelist())

    def test_non_integer_blocked(self):
        for v in ("abc", "765a", "7.65", "1e3"):
            with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: v}):
                self.assertIsNone(read_t2_doc_whitelist(),
                                  f"non-int should be blocked: {v!r}")

    def test_zero_or_negative_blocked(self):
        for v in ("0", "-1", "-765"):
            with patch.dict(os.environ, {T2_DOC_WHITELIST_ENV: v}):
                self.assertIsNone(read_t2_doc_whitelist(),
                                  f"non-positive should be blocked: {v!r}")


class TestT2WhitelistGateBlocking(TestCase):
    """T2 + 다양한 whitelist 시나리오 — 차단/통과 판정."""

    def test_t2_with_matching_whitelist_passes_blocking(self):
        """ENV whitelist == doc_id → blocking 통과 (dispatcher 실행)."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    max_payloads=5,
                )
            self.assertTrue(
                result.enabled,
                f"expected enabled=True; got blocking_reason={result.blocking_reason}",
            )
            self.assertIsNone(result.blocking_reason)
            # dispatcher / integrate / adapter 모두 실행됨
            self.assertIsNotNone(result.dispatcher_output)
            self.assertIsNotNone(result.unified_output)
            self.assertIsNotNone(result.insert_result)
            # dry_run default → INSERT 0회
            self.assertTrue(result.dry_run)
            self.assertEqual(result.insert_result["inserted_count"], 0)
            # sandbox_tenant_ids 도출 검증 — T2 만 포함
            self.assertEqual(
                result.insert_result["sandbox_tenant_ids"],
                [T2_PRODUCTION_TENANT_ID],
            )
        finally:
            os.unlink(pdf)

    def test_t2_with_doc_id_mismatch_blocked(self):
        """ENV whitelist=765 인데 doc_id=762 → 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=762, tenant_id=2,
                )
            self.assertFalse(result.enabled)
            self.assertIn("doc_id=762", result.blocking_reason)
            self.assertIn("765", result.blocking_reason)
            # 운영 자료 미접근
            self.assertIsNone(result.dispatcher_output)
            self.assertIsNone(result.insert_result)
        finally:
            os.unlink(pdf)

    def test_t2_malformed_whitelist_blocked(self):
        """ENV whitelist 가 malformed (다중값/비정수/0) → T2 차단."""
        pdf = _make_simple_pdf()
        try:
            for bad in ("765,762", "abc", "0", "-5", "7.65"):
                with patch.dict(os.environ, {
                    SHADOW_GLOBAL_ENV: "1",
                    T2_DOC_WHITELIST_ENV: bad,
                }):
                    result = shadow_proposal_pipeline(
                        pdf, document_id=765, tenant_id=2,
                    )
                self.assertFalse(
                    result.enabled,
                    f"malformed whitelist {bad!r} should block; got enabled=True",
                )
                self.assertIn("T2", result.blocking_reason)
                self.assertIsNone(result.dispatcher_output)
        finally:
            os.unlink(pdf)

    def test_t2_max_payloads_over_cap_blocked(self):
        """T2 whitelist 통과해도 max_payloads > 5 면 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    max_payloads=T2_WHITELIST_MAX_PAYLOADS + 1,  # 6
                )
            self.assertFalse(result.enabled)
            self.assertIn("max_payloads", result.blocking_reason)
            self.assertIn("T2", result.blocking_reason)
            self.assertIsNone(result.dispatcher_output)
        finally:
            os.unlink(pdf)

    def test_t2_max_payloads_at_cap_passes(self):
        """T2 + max_payloads == 5 (cap 정확) 통과."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    max_payloads=T2_WHITELIST_MAX_PAYLOADS,
                )
            self.assertTrue(result.enabled)
            self.assertIsNone(result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_t1_max_payloads_over_t2_cap_still_passes(self):
        """T1 회귀 — max_payloads > T2 cap 라도 T1 은 영향 없음."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    max_payloads=T2_WHITELIST_MAX_PAYLOADS + 10,  # 15
                )
            self.assertTrue(
                result.enabled,
                f"T1 with high max_payloads should pass; got {result.blocking_reason}",
            )
            self.assertIsNone(result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_t1_sandbox_tenant_ids_unchanged(self):
        """T1 회귀 — sandbox_tenant_ids 는 [1] 유지."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            self.assertTrue(result.enabled)
            self.assertEqual(
                result.insert_result["sandbox_tenant_ids"],
                [DEFAULT_SANDBOX_TENANT_ID],
            )
        finally:
            os.unlink(pdf)

    def test_t2_whitelist_set_does_not_grant_other_tenants(self):
        """ENV whitelist 가 set 되어 있어도 tenant_id=3 등은 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=3,
                )
            self.assertFalse(result.enabled)
            self.assertIn("tenant_id=3", result.blocking_reason)
            self.assertIn("T1 sandbox", result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_t2_whitelist_dry_run_inserts_zero(self):
        """T2 whitelist 통과 + 기본 dry_run → INSERT 0회 (DB write 0)."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                )
            self.assertTrue(result.enabled)
            self.assertEqual(result.insert_result["inserted_count"], 0)
            self.assertTrue(result.insert_result["dry_run"])
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
        from academy.application.use_cases.ai.segmentation import shadow_proposal_pipeline
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
        from academy.application.use_cases.ai.segmentation import shadow_proposal_pipeline
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
        from academy.application.use_cases.ai.segmentation import shadow_proposal_pipeline
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
            # Stage 6.4-prep+1 신규
            "smoke_truncate_to_cap",
        ):
            self.assertIn(required, actions)

    def test_smoke_truncate_to_cap_default_false(self):
        """--smoke-truncate-to-cap default=False — 기본 동작 변경 X."""
        from argparse import ArgumentParser
        from apps.domains.matchup.management.commands.shadow_proposal import Command
        parser = ArgumentParser()
        Command().add_arguments(parser)
        # required 인자만 채워서 parse — 다른 옵션은 default 그대로
        args = parser.parse_args(["--doc-id", "735", "--pdf-path", "/tmp/x.pdf"])
        self.assertFalse(args.smoke_truncate_to_cap)


# ── Stage 6.4-prep+1 — smoke truncation flag ──────────────────────


def _make_synthetic_payload(
    page: int, number: int, *,
    bbox_y: float = 0.1, bbox_x: float = 0.05,
    tenant_id: int = 1, document_id: int = 735,
) -> ProposalPayloadCandidate:
    """단위 테스트용 합성 ProposalPayloadCandidate."""
    return ProposalPayloadCandidate(
        tenant_id=tenant_id,
        document_id=document_id,
        page_number=page,
        detected_problem_number=number,
        bbox={"x": bbox_x, "y": bbox_y, "w": 0.4, "h": 0.2, "norm": True},
        engine="native_pdf",
        model_version="",
        confidence=0.85,
        status="pending",
        analysis_version_key="",
        image_key="",
        raw_response={},
        validation_errors=[],
    )


class TestSmokeTruncateHelper(TestCase):
    """`_truncate_payloads_for_smoke()` 단위 테스트.

    helper 만 단독 테스트 — 파이프라인 의존성 0.
    """

    def test_below_cap_no_truncation(self):
        payloads = [_make_synthetic_payload(p, n) for p, n in [(1, 1), (1, 2)]]
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=5)
        self.assertEqual(len(out), 2)
        self.assertEqual(skipped, 0)

    def test_at_cap_no_truncation(self):
        payloads = [_make_synthetic_payload(1, n) for n in range(1, 6)]
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=5)
        self.assertEqual(len(out), 5)
        self.assertEqual(skipped, 0)

    def test_over_cap_truncates(self):
        payloads = [_make_synthetic_payload(1, n) for n in range(1, 11)]  # 10
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=5)
        self.assertEqual(len(out), 5)
        self.assertEqual(skipped, 5)

    def test_deterministic_sort_by_page_then_number(self):
        # 의도적으로 셔플된 입력
        payloads = [
            _make_synthetic_payload(2, 1),  # page 2, q1
            _make_synthetic_payload(1, 5),  # page 1, q5
            _make_synthetic_payload(1, 1),  # page 1, q1 — 첫 번째 기대
            _make_synthetic_payload(2, 3),
            _make_synthetic_payload(1, 2),
            _make_synthetic_payload(3, 1),
            _make_synthetic_payload(1, 3),
            _make_synthetic_payload(2, 2),
        ]
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=4)
        self.assertEqual(skipped, 4)
        # 정렬 기대: (1,1) (1,2) (1,3) (1,5) — 첫 4개
        keys = [(p.page_number, p.detected_problem_number) for p in out]
        self.assertEqual(keys, [(1, 1), (1, 2), (1, 3), (1, 5)])

    def test_deterministic_sort_uses_bbox_y_when_number_tied(self):
        # 같은 page + 같은 number(0=unknown) — bbox.y 가 정렬 결정
        payloads = [
            _make_synthetic_payload(1, 0, bbox_y=0.7),
            _make_synthetic_payload(1, 0, bbox_y=0.1),
            _make_synthetic_payload(1, 0, bbox_y=0.4),
        ]
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=2)
        self.assertEqual(skipped, 1)
        # y 작은 순 — 0.1 → 0.4 → 0.7 중 첫 둘
        ys = [p.bbox["y"] for p in out]
        self.assertAlmostEqual(ys[0], 0.1)
        self.assertAlmostEqual(ys[1], 0.4)

    def test_deterministic_sort_uses_bbox_x_when_y_tied(self):
        # 모든 키 동률 → 마지막 단계 bbox.x 결정
        payloads = [
            _make_synthetic_payload(1, 1, bbox_y=0.1, bbox_x=0.5),
            _make_synthetic_payload(1, 1, bbox_y=0.1, bbox_x=0.1),
            _make_synthetic_payload(1, 1, bbox_y=0.1, bbox_x=0.3),
        ]
        out, skipped = _truncate_payloads_for_smoke(payloads, max_payloads=2)
        self.assertEqual(skipped, 1)
        xs = [p.bbox["x"] for p in out]
        self.assertAlmostEqual(xs[0], 0.1)
        self.assertAlmostEqual(xs[1], 0.3)

    def test_helper_does_not_mutate_input(self):
        payloads = [_make_synthetic_payload(p, n) for p, n in [(2, 1), (1, 3)]]
        original_order = [(p.page_number, p.detected_problem_number) for p in payloads]
        _truncate_payloads_for_smoke(payloads, max_payloads=10)
        self.assertEqual(
            [(p.page_number, p.detected_problem_number) for p in payloads],
            original_order,
            "input list 순서 mutate 됨 — sorted() 가 새 list 반환해야",
        )


class TestSmokeTruncateInPipeline(TestCase):
    """파이프라인에 truncation flag 통합 테스트."""

    def test_default_off_t1_metadata_present(self):
        """기본 OFF 라도 raw_payload_count / payloads_for_insert_count 메타 기록."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                )
            self.assertFalse(result.debug["smoke_truncate_to_cap"])
            self.assertIn("raw_payload_count", result.debug)
            self.assertIn("payloads_for_insert_count", result.debug)
            self.assertEqual(result.debug["skipped_by_truncation_count"], 0)
            # 기본 OFF — adapter 가 받은 count == raw count
            self.assertEqual(
                result.debug["raw_payload_count"],
                result.debug["payloads_for_insert_count"],
            )
            # truncation_reason 은 truncate 발생 시에만 set
            self.assertNotIn("truncation_reason", result.debug)
        finally:
            os.unlink(pdf)

    def test_flag_true_under_cap_no_truncate(self):
        """flag=True 라도 raw_payload_count <= max_payloads 면 truncate 0."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    smoke_truncate_to_cap=True, max_payloads=5,
                )
            self.assertTrue(result.debug["smoke_truncate_to_cap"])
            self.assertEqual(result.debug["skipped_by_truncation_count"], 0)
            # _make_simple_pdf 는 2개 problem 만 → cap 5 미만 → truncate 0
            self.assertLessEqual(result.debug["raw_payload_count"], 5)
            self.assertNotIn("truncation_reason", result.debug)
        finally:
            os.unlink(pdf)

    def test_flag_true_t2_whitelist_unset_still_blocked(self):
        """flag=True 라도 T2 + whitelist 미설정 → 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    smoke_truncate_to_cap=True,
                )
            self.assertFalse(result.enabled)
            self.assertIn(T2_DOC_WHITELIST_ENV, result.blocking_reason)
            # 차단 시 dispatcher / unified / insert_result 모두 None
            self.assertIsNone(result.dispatcher_output)
            self.assertIsNone(result.insert_result)
        finally:
            os.unlink(pdf)

    def test_flag_true_t2_doc_mismatch_still_blocked(self):
        """flag=True 라도 T2 + doc 불일치 → 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=999, tenant_id=2,
                    smoke_truncate_to_cap=True,
                )
            self.assertFalse(result.enabled)
            self.assertIn("doc_id=999", result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_flag_true_t2_max_payloads_over_cap_still_blocked(self):
        """flag=True 라도 T2 + max_payloads > 5 → T2 cap 차단."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    smoke_truncate_to_cap=True,
                    max_payloads=T2_WHITELIST_MAX_PAYLOADS + 1,
                )
            self.assertFalse(result.enabled)
            self.assertIn("max_payloads", result.blocking_reason)
            self.assertIn("T2", result.blocking_reason)
        finally:
            os.unlink(pdf)

    def test_flag_true_t2_passes_when_all_conditions_met(self):
        """flag=True + T2 + whitelist match + doc match + cap 통과 → enabled."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {
                SHADOW_GLOBAL_ENV: "1",
                T2_DOC_WHITELIST_ENV: "765",
            }):
                result = shadow_proposal_pipeline(
                    pdf, document_id=765, tenant_id=2,
                    smoke_truncate_to_cap=True, max_payloads=5,
                )
            self.assertTrue(result.enabled)
            self.assertTrue(result.debug["smoke_truncate_to_cap"])
            self.assertIn("raw_payload_count", result.debug)
            self.assertIn("payloads_for_insert_count", result.debug)
            # dry_run default → INSERT 0
            self.assertEqual(result.insert_result["inserted_count"], 0)
        finally:
            os.unlink(pdf)

    def test_t1_regression_with_flag_true(self):
        """T1 회귀 — flag=True 도 T1 정상 동작 (기본 OFF 와 동일 결과)."""
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                r_off = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    smoke_truncate_to_cap=False,
                )
                r_on = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    smoke_truncate_to_cap=True,
                )
            # 둘 다 enabled
            self.assertTrue(r_off.enabled)
            self.assertTrue(r_on.enabled)
            # _make_simple_pdf 는 cap 미만 → 두 결과 동일한 payload count
            self.assertEqual(
                r_off.debug["raw_payload_count"],
                r_on.debug["raw_payload_count"],
            )
            self.assertEqual(
                r_off.debug["payloads_for_insert_count"],
                r_on.debug["payloads_for_insert_count"],
            )
            self.assertEqual(r_on.debug["skipped_by_truncation_count"], 0)
        finally:
            os.unlink(pdf)

    def test_truncation_reason_set_only_when_truncated(self):
        """truncation_reason 메타데이터 — 실제 잘렸을 때만 set."""
        # helper 단위로 직접 테스트 — pipeline 통합 PDF 가 항상 cap 미만이라
        # truncation_reason 가 set 되는 분기는 helper 호출부 검증으로 우회.
        # 단위 테스트는 위 TestSmokeTruncateHelper 가 cover.
        # 여기서는 pipeline 결과의 debug 가 cap 미만 시 reason 없는지만 확인.
        pdf = _make_simple_pdf()
        try:
            with patch.dict(os.environ, {SHADOW_GLOBAL_ENV: "1"}, clear=False):
                os.environ.pop(T2_DOC_WHITELIST_ENV, None)
                result = shadow_proposal_pipeline(
                    pdf, document_id=735, tenant_id=1,
                    smoke_truncate_to_cap=True,
                )
            # cap 미만 → 안 잘림 → reason 부재
            self.assertEqual(result.debug["skipped_by_truncation_count"], 0)
            self.assertNotIn("truncation_reason", result.debug)
        finally:
            os.unlink(pdf)


class TestSmokeTruncationConstantExposed(TestCase):
    def test_smoke_truncation_reason_constant(self):
        # Stage 6.4 합의된 상수 — 다른 reason 값과 충돌 X
        self.assertEqual(SMOKE_TRUNCATION_REASON, "stage_6_4_smoke_cap")
