"""Stage 6.0 (2026-05-07) — proposal insert adapter 단위 테스트.

검증:
- dry_run=True default → INSERT 0회
- allow_insert=False → INSERT 0회 (dry_run=False 라도)
- sandbox_tenant_ids 없으면 INSERT 차단
- payload tenant_id ∉ sandbox_tenant_ids → 전체 batch 차단
- max_payload_count 초과 시 차단
- payload.status='approved' INSERT 차단 (block_approved)
- payload.status='rejected' status 유지 (INSERT 가능 시 status='rejected' 그대로)
- prepare_proposal_insert 가 create_proposal 호출 kwargs 와 1:1 매핑
- 운영 callback (`apps.domains.ai.callbacks._handle_matchup_*`) 미import
- selected_problem_ids / MatchupProblem 미접근
- OCR/VLM SDK / DB 모델 .objects.create 직접 호출 0회
"""
from __future__ import annotations

from typing import Any
from unittest import TestCase
from unittest.mock import patch, MagicMock

from apps.domains.matchup.segmentation.mock_response_integrator import (
    ProposalPayloadCandidate, ValidationError,
)
from apps.domains.matchup.segmentation.proposal_insert_adapter import (
    InsertDecision, InsertSandboxResult, SCHEMA_VERSION,
    insert_proposal_sandbox, prepare_proposal_insert,
    result_to_dict, validate_before_insert,
)


def _ok_payload(**overrides) -> ProposalPayloadCandidate:
    base = dict(
        tenant_id=999, document_id=99, page_number=0,
        detected_problem_number=1,
        bbox={"x": 0.10, "y": 0.20, "w": 0.50, "h": 0.30, "norm": True},
        engine="vlm", model_version="mock-1", confidence=0.85,
        status="pending",
        analysis_version_key="batch-x", image_key="",
        raw_response={}, validation_errors=[],
    )
    base.update(overrides)
    return ProposalPayloadCandidate(**base)


# ── prepare_proposal_insert ────────────────────────────────────


class PrepareProposalInsertTests(TestCase):
    def test_full_kwargs_match_create_proposal_signature(self):
        kwargs = prepare_proposal_insert(_ok_payload())
        # create_proposal signature 의 모든 keyword
        expected_keys = {
            "tenant_id", "document_id", "page_number", "detected_problem_number",
            "bbox", "engine", "model_version", "confidence",
            "image_key", "raw_response", "analysis_version_key", "auto_status",
        }
        self.assertEqual(set(kwargs.keys()), expected_keys)

    def test_status_maps_to_auto_status(self):
        kwargs = prepare_proposal_insert(_ok_payload(status="needs_review"))
        self.assertEqual(kwargs["auto_status"], "needs_review")

    def test_bbox_dict_passed_as_dict(self):
        kwargs = prepare_proposal_insert(_ok_payload())
        self.assertEqual(kwargs["bbox"]["norm"], True)
        self.assertIn("x", kwargs["bbox"])

    def test_validation_errors_not_in_kwargs(self):
        # validation_errors 는 helper 가 자체 생성
        kwargs = prepare_proposal_insert(_ok_payload(validation_errors=[
            ValidationError(code="manual_overlap", detail="x"),
        ]))
        self.assertNotIn("validation_errors", kwargs)


# ── validate_before_insert ────────────────────────────────────


class ValidateBeforeInsertTests(TestCase):
    def test_ok_payload_passes(self):
        ok, reason, violations = validate_before_insert(_ok_payload())
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertEqual(violations, [])

    def test_approved_status_blocked_by_default(self):
        ok, reason, violations = validate_before_insert(_ok_payload(status="approved"))
        self.assertFalse(ok)
        self.assertEqual(reason, "status_approved_blocked")
        self.assertGreater(len(violations), 0)

    def test_approved_status_allowed_when_block_approved_false(self):
        ok, _, _ = validate_before_insert(
            _ok_payload(status="approved"), block_approved=False,
        )
        # approved 가 STATUS_CHOICES 안이고 schema/field 통과면 ok=True
        self.assertTrue(ok)

    def test_invalid_engine_blocked(self):
        ok, reason, _ = validate_before_insert(_ok_payload(engine="weird"))
        self.assertFalse(ok)
        self.assertEqual(reason, "field_violation")

    def test_invalid_status_blocked(self):
        ok, reason, _ = validate_before_insert(_ok_payload(status="weird"))
        self.assertFalse(ok)


# ── insert_proposal_sandbox — dry_run / allow_insert path ─────────


class DryRunDefaultTests(TestCase):
    def test_dry_run_true_default_no_insert(self):
        # 기본값 dry_run=True / allow_insert=False
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox([_ok_payload(), _ok_payload()])
            mock_import.assert_not_called()  # create_proposal import 도 안 함
        self.assertTrue(r.dry_run)
        self.assertFalse(r.allow_insert)
        self.assertEqual(r.inserted_count, 0)
        self.assertEqual(r.dry_run_count, 2)
        for d in r.decisions:
            self.assertEqual(d.status, "dry_run")

    def test_allow_insert_false_no_insert_even_when_dry_run_false(self):
        # dry_run=False 라도 allow_insert=False 면 INSERT 0회
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=False,
            )
            mock_import.assert_not_called()
        self.assertEqual(r.inserted_count, 0)
        self.assertEqual(r.dry_run_count, 1)
        self.assertTrue(r.dry_run)

    def test_dry_run_records_validation_for_each_payload(self):
        r = insert_proposal_sandbox([
            _ok_payload(),
            _ok_payload(engine="weird"),  # field_violation
        ])
        self.assertEqual(r.dry_run_count, 2)
        # decision 0 — ok, decision 1 — field_violation
        self.assertIn("validation=ok", r.decisions[0].reason)
        self.assertIn("field_violation", r.decisions[1].reason)


class SandboxGateTests(TestCase):
    def test_no_sandbox_tenant_ids_blocks(self):
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=None,
            )
            mock_import.assert_not_called()
        self.assertEqual(r.inserted_count, 0)
        self.assertIsNotNone(r.blocking_reason)
        self.assertIn("no sandbox_tenant_ids", r.blocking_reason)
        self.assertTrue(all(d.status == "skipped_blocking" for d in r.decisions))

    def test_empty_sandbox_tenant_ids_blocks(self):
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[],
            )
            mock_import.assert_not_called()
        self.assertEqual(r.inserted_count, 0)
        self.assertIsNotNone(r.blocking_reason)

    def test_payload_tenant_not_in_sandbox_blocks(self):
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox(
                [_ok_payload(tenant_id=2)],   # production tenant
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
            )
            mock_import.assert_not_called()
        self.assertEqual(r.inserted_count, 0)
        self.assertIsNotNone(r.blocking_reason)
        self.assertIn("not in sandbox_tenant_ids", r.blocking_reason)

    def test_max_payload_count_blocks(self):
        payloads = [_ok_payload() for _ in range(101)]
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
        ) as mock_import:
            r = insert_proposal_sandbox(
                payloads, dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999], max_payload_count=100,
            )
            mock_import.assert_not_called()
        self.assertEqual(r.inserted_count, 0)
        self.assertIsNotNone(r.blocking_reason)
        self.assertIn("max_payload_count", r.blocking_reason)


class SandboxInsertTests(TestCase):
    def test_sandbox_path_calls_create_proposal(self):
        # mock create_proposal 으로 INSERT path 만 검증 (실 DB X)
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                # 기존 mock 테스트는 idempotent ORM lookup 미사용 (DB 미접근)
                existing_lookup_fn=lambda key: None,
            )
        self.assertEqual(r.inserted_count, 1)
        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], 999)
        self.assertEqual(kwargs["auto_status"], "pending")

    def test_approved_payload_skipped_in_sandbox(self):
        # approved status payload 는 INSERT 안 함
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload(status="approved"), _ok_payload(status="pending")],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: None,
            )
        # approved 는 skipped_status_approved, pending 은 inserted
        self.assertEqual(r.inserted_count, 1)
        statuses = [d.status for d in r.decisions]
        self.assertIn("skipped_status_approved", statuses)
        self.assertIn("inserted", statuses)
        self.assertEqual(mock_create.call_count, 1)

    def test_rejected_payload_status_preserved(self):
        # rejected payload 는 INSERT 가능 (status='rejected' 그대로)
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload(status="rejected", validation_errors=[
                    ValidationError(code="manual_overlap", detail="x"),
                ])],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: None,
            )
        # status=rejected payload 도 INSERT 가능 — status 유지
        self.assertEqual(r.rejected_count, 1)
        if mock_create.call_args:
            kwargs = mock_create.call_args.kwargs
            self.assertEqual(kwargs["auto_status"], "rejected")

    def test_invalid_payload_skipped_validation(self):
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload(engine="weird")],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: None,
            )
        self.assertEqual(r.inserted_count, 0)
        self.assertEqual(r.skipped_count, 1)
        self.assertEqual(r.decisions[0].status, "skipped_validation")
        mock_create.assert_not_called()


# ── 운영 안전성 regression ──────────────────────────────────────


class RegressionTests(TestCase):
    def test_no_callback_imports(self):
        """운영 callback path import 0회 — _handle_matchup_* 어디서도 안 보임."""
        from apps.domains.matchup.segmentation import proposal_insert_adapter
        import inspect
        src = inspect.getsource(proposal_insert_adapter)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "_handle_matchup_ai_result", "_handle_matchup_index_result",
            "_handle_matchup_manual_result",
            "from apps.domains.ai.callbacks",
            "from apps.domains.ai.gateway",
            "dispatch_job(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"adapter 에서 운영 callback access '{token}' 발견")

    def test_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import proposal_insert_adapter
        import inspect
        src = inspect.getsource(proposal_insert_adapter)
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
            "import boto3", "from boto3",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"adapter 에서 실 SDK import '{token}' 발견")

    def test_no_matchup_problem_or_selected_access(self):
        """code-level 에서 selected_problem_ids 또는 MatchupProblem .objects access 0회.

        docstring / 주석 mention 은 허용 (사용자 directive 준수 명시 목적).
        AST 로 Attribute / Name / Call 검사.
        """
        from apps.domains.matchup.segmentation import proposal_insert_adapter
        import ast, inspect
        tree = ast.parse(inspect.getsource(proposal_insert_adapter))

        # 직접 access 패턴: MatchupProblem.objects / .selected_problem_ids /
        # .objects.create / .objects.bulk_create
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                full = ast.unparse(node)
                if "selected_problem_ids" in full and "value=" not in full:
                    # ast.unparse() 로 attribute access 만 — string literal 제외
                    if isinstance(node.value, ast.Name) or isinstance(node.value, ast.Attribute):
                        violations.append(f"Attribute access: {full}")
                if (
                    "MatchupProblem.objects" in full
                    or ".objects.create" in full
                    or ".objects.bulk_create" in full
                ):
                    violations.append(f"Forbidden access: {full}")
        self.assertEqual(
            violations, [],
            f"adapter 에서 금지 access 발견: {violations}",
        )

    def test_lazy_import_only_in_sandbox_path(self):
        """create_proposal import 가 module-level 이 아니라 함수 내부에 있는지."""
        from apps.domains.matchup.segmentation import proposal_insert_adapter
        import inspect
        # module-level import 검사 (함수 def 시작 전)
        src = inspect.getsource(proposal_insert_adapter)
        head = src.split("def ")[0]   # 첫 번째 def 이전 부분 = module-level
        self.assertNotIn("from apps.domains.matchup.proposal_helpers", head)
        self.assertNotIn("from apps.domains.matchup.models", head)


class ResultToDictTests(TestCase):
    def test_serializable(self):
        r = insert_proposal_sandbox([_ok_payload()])
        d = result_to_dict(r)
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertIn("decisions", d)
        import json
        json.dumps(d, default=str)


# ── Stage 6.2A: idempotency ──────────────────────────────────────


from apps.domains.matchup.segmentation.proposal_insert_adapter import (  # noqa: E402
    _idempotent_key,
)


class IdempotentKeyTests(TestCase):
    def test_same_payload_same_key(self):
        a = _ok_payload()
        b = _ok_payload()
        self.assertEqual(_idempotent_key(a), _idempotent_key(b))

    def test_different_page_different_key(self):
        a = _ok_payload(page_number=0)
        b = _ok_payload(page_number=1)
        self.assertNotEqual(_idempotent_key(a), _idempotent_key(b))

    def test_different_problem_number_different_key(self):
        a = _ok_payload(detected_problem_number=1)
        b = _ok_payload(detected_problem_number=2)
        self.assertNotEqual(_idempotent_key(a), _idempotent_key(b))

    def test_different_version_key_different_key(self):
        a = _ok_payload(analysis_version_key="batch-A")
        b = _ok_payload(analysis_version_key="batch-B")
        self.assertNotEqual(_idempotent_key(a), _idempotent_key(b))

    def test_empty_version_key_falls_back_to_engine_bbox(self):
        # version_key 비어있으면 fallback — engine + bbox 추가
        a = _ok_payload(analysis_version_key="", engine="vlm")
        b = _ok_payload(analysis_version_key="", engine="ocr")
        self.assertNotEqual(_idempotent_key(a), _idempotent_key(b))
        # 같은 engine + 같은 bbox 면 동일
        c = _ok_payload(analysis_version_key="", engine="vlm")
        self.assertEqual(_idempotent_key(a), _idempotent_key(c))

    def test_empty_version_key_different_bbox_different_key(self):
        a = _ok_payload(analysis_version_key="",
                        bbox={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "norm": True})
        b = _ok_payload(analysis_version_key="",
                        bbox={"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5, "norm": True})
        self.assertNotEqual(_idempotent_key(a), _idempotent_key(b))


class IdempotentSandboxTests(TestCase):
    def test_existing_lookup_skips_insert(self):
        # existing_lookup_fn 이 id 반환 → INSERT 안 함, decision='skipped_idempotent'
        mock_create = MagicMock(return_value=MagicMock(id=99))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: 12345,   # 기존 id
            )
        self.assertEqual(r.inserted_count, 0)
        self.assertEqual(r.skipped_count, 1)
        mock_create.assert_not_called()
        d = r.decisions[0]
        self.assertEqual(d.status, "skipped_idempotent")
        self.assertEqual(d.inserted_proposal_id, 12345)

    def test_idempotent_check_disabled(self):
        # idempotent_check=False → lookup 미호출 / INSERT 진행
        mock_create = MagicMock(return_value=MagicMock(id=42))
        called: list[Any] = []
        def lookup(key):
            called.append(key)
            return 12345
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                idempotent_check=False,
                existing_lookup_fn=lookup,
            )
        self.assertEqual(called, [])  # idempotent_check=False → lookup 미호출
        self.assertEqual(r.inserted_count, 1)

    def test_lookup_returning_none_inserts(self):
        # existing_lookup_fn 이 None 반환 → INSERT 진행
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: None,
            )
        self.assertEqual(r.inserted_count, 1)
        self.assertEqual(r.skipped_count, 0)

    def test_lookup_raising_skips_safely(self):
        # lookup 예외 → 보수적 skipped_validation (INSERT 안 함)
        mock_create = MagicMock(return_value=MagicMock(id=42))
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=[999],
                existing_lookup_fn=lambda key: (_ for _ in ()).throw(RuntimeError("db")),
            )
        self.assertEqual(r.inserted_count, 0)
        self.assertEqual(r.skipped_count, 1)
        mock_create.assert_not_called()

    def test_default_lookup_fn_is_lazy(self):
        """idempotent_check=True 인데 existing_lookup_fn=None 면 _default_existing_lookup 사용.

        본 unit test 는 ORM 미접근 — sandbox path 진입 자체가 차단되도록 sandbox=[] 로
        통과 안 하게.
        """
        # sandbox 차단된 case 에선 lookup_fn 자체 호출 0회
        mock_create = MagicMock()
        with patch(
            "apps.domains.matchup.segmentation.proposal_insert_adapter._import_create_proposal",
            return_value=mock_create,
        ):
            r = insert_proposal_sandbox(
                [_ok_payload()],
                dry_run=False, allow_insert=True,
                sandbox_tenant_ids=None,  # gate 차단
            )
        self.assertEqual(r.inserted_count, 0)
        self.assertIsNotNone(r.blocking_reason)
        mock_create.assert_not_called()
