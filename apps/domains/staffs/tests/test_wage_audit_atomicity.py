from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.domains.staffs.models import Staff, StaffWorkType, WorkType


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    BILLING_TEST_BYPASS_SUBSCRIPTION=True,
)
class WageAuditAtomicityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="QA wage audit", code="qa-wage-audit")
        self.owner = get_user_model().objects.create_user(username="qa-wage-owner")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.staff = Staff.objects.create(tenant=self.tenant, name="QA 직원")
        self.kind = WorkType.objects.create(tenant=self.tenant, name="QA 시급", base_hourly_wage=12000)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def request(self, method, path, data=None):
        return getattr(self.client, method)(
            f"/api/v1/staffs/{path}", data, format="json",
            HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code,
        )

    def fail_audit(self, method, path, data=None):
        # Exercise the real best-effort recorder's exception -> None contract.
        with patch("apps.core.models.OpsAuditLog.objects.create", side_effect=RuntimeError("QA audit unavailable")):
            response = self.request(method, path, data)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertIn("다시", str(response.data))
        self.assertEqual(OpsAuditLog.objects.count(), 0)

    def assert_audit(self, action, **payload):
        log = OpsAuditLog.objects.get(action=action)
        self.assertEqual(log.actor_user_id, self.owner.id)
        self.assertEqual(log.target_tenant_id, self.tenant.id)
        for key, value in payload.items():
            self.assertEqual(log.payload[key], value)

    def test_work_type_create_failure_preserves_state_and_retry_creates_once(self):
        data = {"name": "QA 신규", "base_hourly_wage": 15000}
        self.fail_audit("post", "work-types/", data)
        self.assertFalse(WorkType.objects.filter(name=data["name"]).exists())
        self.assertEqual(self.request("post", "work-types/", data).status_code, 201)
        self.assertEqual(WorkType.objects.filter(name=data["name"]).count(), 1)
        self.assert_audit("staff.work_type_created", base_hourly_wage=15000)

    def test_work_type_update_failure_preserves_wage_and_retry_has_old_value(self):
        path = f"work-types/{self.kind.id}/"
        self.fail_audit("patch", path, {"base_hourly_wage": 18000})
        self.kind.refresh_from_db()
        self.assertEqual(self.kind.base_hourly_wage, 12000)
        self.assertEqual(self.request("patch", path, {"base_hourly_wage": 18000}).status_code, 200)
        self.kind.refresh_from_db()
        self.assertEqual(self.kind.base_hourly_wage, 18000)
        self.assert_audit("staff.work_type_updated", old_base_hourly_wage=12000, new_base_hourly_wage=18000)

    def test_work_type_delete_failure_keeps_assignment_then_retry_deletes_both(self):
        assignment = StaffWorkType.objects.create(tenant=self.tenant, staff=self.staff, work_type=self.kind)
        path = f"work-types/{self.kind.id}/"
        kind_id = self.kind.id
        self.fail_audit("delete", path)
        self.assertTrue(WorkType.objects.filter(id=kind_id).exists())
        self.assertTrue(StaffWorkType.objects.filter(id=assignment.id).exists())
        self.assertEqual(self.request("delete", path).status_code, 204)
        self.assertFalse(WorkType.objects.filter(id=kind_id).exists())
        self.assertFalse(StaffWorkType.objects.filter(id=assignment.id).exists())
        self.assert_audit("staff.work_type_deleted", work_type_id=kind_id)

    def test_assignment_create_failure_preserves_state_and_retry_creates_once(self):
        data = {"staff": self.staff.id, "work_type_id": self.kind.id, "hourly_wage": 15000}
        self.fail_audit("post", "staff-work-types/", data)
        self.assertFalse(StaffWorkType.objects.exists())
        self.assertEqual(self.request("post", "staff-work-types/", data).status_code, 201)
        self.assertEqual(StaffWorkType.objects.count(), 1)
        self.assert_audit("staff.staff_work_type_created", old_hourly_wage=None, new_hourly_wage=15000)

    def test_assignment_update_failure_preserves_zero_wage_and_retry_has_old_value(self):
        assignment = StaffWorkType.objects.create(tenant=self.tenant, staff=self.staff, work_type=self.kind, hourly_wage=0)
        path = f"staff-work-types/{assignment.id}/"
        self.fail_audit("patch", path, {"hourly_wage": 15000})
        assignment.refresh_from_db()
        self.assertEqual(assignment.hourly_wage, 0)
        self.assertEqual(self.request("patch", path, {"hourly_wage": 15000}).status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.hourly_wage, 15000)
        self.assert_audit("staff.staff_work_type_updated", old_hourly_wage=0, new_hourly_wage=15000)

    def test_assignment_delete_failure_preserves_wage_and_retry_deletes_once(self):
        assignment = StaffWorkType.objects.create(tenant=self.tenant, staff=self.staff, work_type=self.kind, hourly_wage=15000)
        path = f"staff-work-types/{assignment.id}/"
        self.fail_audit("delete", path)
        self.assertTrue(StaffWorkType.objects.filter(id=assignment.id, hourly_wage=15000).exists())
        self.assertEqual(self.request("delete", path).status_code, 204)
        self.assertFalse(StaffWorkType.objects.filter(id=assignment.id).exists())
        self.assert_audit("staff.staff_work_type_deleted", assignment_id=assignment.id, old_hourly_wage=15000)
