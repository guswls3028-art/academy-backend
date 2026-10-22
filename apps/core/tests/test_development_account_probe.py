"""Exercise the candidate probe against real account models and HTTP views."""
import ast
import hashlib
import hmac
import importlib.util
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from apps.core.management.commands.setup_ymath_realuse_scenario import Command
from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.account_credentials import remember_account_password
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "development_account_probe_contract", ROOT / "scripts/v1/probe-account-registration-development.py"
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


@override_settings(ALLOWED_HOSTS=["127.0.0.1"], TENANT_HEADER_CODE_ALLOWED_HOSTS=["127.0.0.1"])
class DevelopmentAccountProbeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            code="qa-ymath-realuse-fe-123-1-abcdef123456", name="Synthetic account probe", is_active=True
        )
        self.student_user = self.make_user("synthetic-student", "01087651234", "student")
        self.parent_user = self.make_user("01087655678", "01087655678", "parent")
        self.parent = Parent.objects.create(
            tenant=self.tenant, user=self.parent_user, name="Synthetic Parent", phone=self.parent_user.phone
        )
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.student_user, parent=self.parent,
            ps_number="synthetic-student", omr_code="87651234",
            name=PROBE.fixture_prefix(self.tenant.code) + "phone",
            phone=self.student_user.phone, parent_phone=self.parent.phone,
        )

    def make_user(self, username, phone, role):
        user = get_user_model().objects.create_user(
            username=user_internal_username(self.tenant, username), tenant=self.tenant,
            phone=phone, password=phone[-4:],
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        remember_account_password(user, phone[-4:])
        return user

    def open_request(self, request, timeout):
        self.assertEqual(timeout, 15)
        target = urlsplit(request.full_url)
        self.assertEqual(target.netloc, "127.0.0.1:8000")
        headers = {"HTTP_" + name.upper().replace("-", "_"): value
                   for name, value in request.header_items() if name.lower() != "content-type"}
        response = self.client.generic(
            request.get_method(), target.path, data=request.data or b"",
            content_type="application/json", HTTP_HOST="127.0.0.1:8000", **headers,
        )

        class Reply:
            status = response.status_code

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return response.content[:size]

        return Reply()

    def test_real_credentials_roles_and_child_projection_pass_without_business_changes(self):
        with patch.object(PROBE, "runtime_scope", return_value="a" * 64), \
                patch.object(PROBE.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = self.open_request
            result = PROBE.run_probe(self.tenant, self.student.pk, "verify", "phone_last4")
        self.assertTrue(result["credentials_valid"])
        self.assertTrue(result["token_me_valid"])
        self.assertEqual(result["business_mutations"], 0)
        self.assertEqual(result["snapshot_count"], 0)

    def test_registered_accounts_pass_probe_with_their_own_phone_passwords(self):
        from apps.domains.students.services.creation import create_student_account

        student_name = (PROBE.fixture_prefix(self.tenant.code) + "긴학생이름" * 3)[:50]
        created = create_student_account(
            tenant=self.tenant, password_mode="phone_last4", parent_password_mode="phone_last4",
            student_data={"name": student_name,
                          "ps_number": "registered-student", "phone": "01087659876",
                          "parent_phone": "01087655432"},
        )
        created.student.refresh_from_db()
        self.assertEqual(created.student.name, student_name)
        self.assertEqual(created.student.user.name, student_name)
        self.assertEqual(created.parent.name, student_name[:46] + " 학부모")
        self.assertEqual(created.parent.user.name, created.parent.name)
        with patch.object(PROBE, "runtime_scope", return_value="a" * 64), \
                patch.object(PROBE.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = self.open_request
            result = PROBE.run_probe(self.tenant, created.student.pk, "verify", "phone_last4")
        self.assertTrue(result["credentials_valid"])
        self.assertTrue(result["token_me_valid"])
        self.assertEqual(result["business_mutations"], 0)


class AccountProbeTransactionTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_fixed_action_releases_tenant_lock_before_http_probe(self):
        # TestCase hides this defect inside its implicit transaction; use real
        # PostgreSQL autocommit and the AccountProbe branch from the fixed document.
        self.assertFalse(connection.in_atomic_block)
        tenant = Tenant.objects.create(code="qa-ymath-realuse-fe-123-1-abcdef123456", name="Probe lock")
        with self.assertRaises(TransactionManagementError):
            Command._exact_tenant_or_fail_on_case_variant(tenant.code)
        document = json.loads((ROOT / "scripts/v1/templates/ssm/frontend_development_qa.json").read_text())
        shell = shlex.split(document["properties"]["linux"]["commands"], posix=True)[2]
        source = shell.split("<<'ACADEMY_QA_PY'\n", 1)[1].rsplit("ACADEMY_QA_PY", 1)[0]
        functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                     and node.name in {"ownership_payload", "assert_cleanup_owner", "run"}]
        run = next(node for node in functions if node.name == "run")
        run.body = [next(node for node in run.body if isinstance(node, ast.If) and any(
            isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "run_probe"
            for item in ast.walk(node)
        ))]
        namespace = {
            "hashlib": hashlib, "hmac": hmac, "re": re, "transaction": transaction,
            "OpsAuditLog": OpsAuditLog, "action": "AccountProbe", "command": Command(),
            "tenant": tenant.code, "expected_tenant_id": tenant.pk, "capability": "a" * 64,
            "account_student_id": 23, "account_mode": "verify", "account_kind": "phone_last4",
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "fixed-account-action", "exec"), namespace)
        OpsAuditLog.objects.create(
            action="development.qa.setup", target_tenant=tenant, result="success",
            payload=namespace["ownership_payload"](tenant.code, tenant.pk, "a" * 64),
        )

        def authenticate(subject, *_args):
            self.assertEqual(subject.pk, tenant.pk)
            self.assertFalse(connection.in_atomic_block, "HTTP authentication must not hold the tenant row lock")
            return {"sanitized": True}

        probe = SimpleNamespace(run_probe=Mock(side_effect=authenticate))
        probe_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))
        real_spec = importlib.util.spec_from_file_location
        real_module = importlib.util.module_from_spec
        with patch("importlib.util.spec_from_file_location", side_effect=lambda name, path:
                   probe_spec if str(path).endswith("probe-account-registration-development.py") else real_spec(name, path)), \
                patch("importlib.util.module_from_spec", side_effect=lambda spec:
                      probe if spec is probe_spec else real_module(spec)):
            self.assertEqual(namespace["run"]({}), {"sanitized": True})
        probe.run_probe.assert_called_once_with(tenant, 23, "verify", "phone_last4")
        self.assertFalse(connection.in_atomic_block)
