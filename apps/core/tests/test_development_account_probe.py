"""Exercise the candidate probe against real account models and HTTP views."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantMembership
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

        created = create_student_account(
            tenant=self.tenant, password_mode="phone_last4", parent_password_mode="phone_last4",
            student_data={"name": PROBE.fixture_prefix(self.tenant.code) + "registered",
                          "ps_number": "registered-student", "phone": "01087659876",
                          "parent_phone": "01087655432"},
        )
        with patch.object(PROBE, "runtime_scope", return_value="a" * 64), \
                patch.object(PROBE.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = self.open_request
            result = PROBE.run_probe(self.tenant, created.student.pk, "verify", "phone_last4")
        self.assertTrue(result["credentials_valid"])
        self.assertTrue(result["token_me_valid"])
        self.assertEqual(result["business_mutations"], 0)
