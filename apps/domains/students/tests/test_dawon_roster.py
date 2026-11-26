from datetime import time, timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import NotificationLog
from apps.domains.staffs.models import PayrollSnapshot, Staff, WorkMonthLock, WorkRecord, WorkType
from apps.domains.students.models import Student, Tag
from apps.domains.students.services.dawon_staff import EMPLOYEE, FIXTURES, WORK_MARK, WORK_TYPE


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonRosterTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.other = Tenant.objects.create(code="other-qa", name="기타 학원")
        self.users = {}
        for role in ("admin", "teacher"):
            user = get_user_model().objects.create_user(username="t719_dawon-demo-" + role, tenant=self.tenant)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
            self.users[role] = user
        self.factory = APIRequestFactory()
        self.legacy_user = get_user_model().objects.create_user(username="t719_legacy", tenant=self.tenant)
        extra_user = get_user_model().objects.create_user(username="t719_other", tenant=self.tenant)
        Student.objects.create(tenant=self.tenant, user=extra_user, ps_number="other", omr_code="00000000", name="보존")
        self.legacy = Student.objects.create(
            tenant=self.tenant, user=self.legacy_user, ps_number="legacy", omr_code="00000001", name="예시 보존",
        )

    def call(self, method, suffix="", payload=None, role="admin", tenant=None, token=True, api="demo-roster"):
        path = f"/api/v1/students/{api}/" + suffix
        request = getattr(self.factory, method)(path, payload or {}, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=self.users[role], token={"dawon_demo_role": role} if token else None)
        route = resolve(path)
        return route.func(request, **route.kwargs)

    def test_create_retry_edit_tag_delete_and_restore_only_owned_students(self):
        tag = Tag.objects.create(tenant=self.tenant, name="검수 대상")
        payload = {"request_id": str(uuid4()), "students": [{"name": str(name), "school": "샘플고"} for name in (1, 2, 3)]}
        first = self.call("post", payload=payload)
        self.assertEqual(first.status_code, 201)
        id_ = first.data["rows"][0]["id"]
        self.assertEqual(self.call("post", payload=payload).data, first.data)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 5)
        self.assertFalse(Student.objects.get(pk=id_).user.has_usable_password())
        route = f"{id_}/"
        changed = self.call("patch", route, {"name": "수정됨", "tag_ids": [tag.pk]})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data["tag_ids"], [tag.pk])
        self.assertEqual(Student.objects.get(pk=id_).user.name, "수정됨")
        self.assertEqual(self.call("delete", f"{self.legacy.pk}/").status_code, 404)
        self.assertEqual(self.call("patch", f"{self.legacy.pk}/", {"name": "보호"}).status_code, 404)
        self.assertEqual(self.call("delete", route).status_code, 204)
        self.assertEqual(self.call("get", payload={"deleted": "true"}).data["rows"][0]["id"], id_)
        self.assertEqual(self.call("post", route).status_code, 200)
        self.assertIsNone(Student.objects.get(pk=id_).deleted_at)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.name, "예시 보존")

    def test_wrong_roles_tenants_and_contact_fields_fail_closed(self):
        self.assertEqual(self.call("get", role="teacher").status_code, 404)
        self.assertEqual(self.call("get", tenant=self.other).status_code, 404)
        self.assertEqual(self.call("get", token=False).status_code, 404)
        payload = {"request_id": str(uuid4()), "students": [{"name": "검수"}, {"name": "거절", "phone": "01000000000"}]}
        self.assertEqual(self.call("post", payload=payload).status_code, 400)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 2)


    def test_credential_is_salted_persisted_and_unusable_at_normal_login(self):
        cache.clear()
        payload = {"request_id": str(uuid4()), "students": [{"name": "인증 검수"}]}
        id_ = self.call("post", payload=payload).data["rows"][0]["id"]
        route = f"{id_}/credential/"
        choice = {"action": "set", "password": "test-only-pass"}
        self.assertEqual(self.call("post", route, choice).data, {"configured": True})
        user = Student.objects.get(pk=id_).user
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password(choice["password"]))
        self.assertNotIn(choice["password"], user.password)
        self.assertEqual(self.call("post", route, {**choice, "action": "check"}).data, {"matches": True})
        self.assertEqual(self.call("post", route, {"action": "check", "password": "incorrect"}).data, {"matches": False})
        self.assertEqual(self.call("post", f"{self.legacy.pk}/credential/", choice).status_code, 404)
        self.assertEqual(self.call("get").data["rows"][0]["id"], id_)

    def test_preview_record_is_immutable_idempotent_and_not_a_delivery(self):
        api = "demo-messages"
        payload = {"request_id": str(uuid4()), "template_id": "notice", "student_ids": [self.legacy.pk]}
        templates = self.call("get", api=api).data["templates"]
        first = self.call("post", payload=payload, api=api)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data["body"], templates[0]["body"])
        self.assertEqual(first.data["audience"], "parent")
        self.assertEqual(first.data["recipient_count"], 1)
        self.assertTrue(first.data["simulated"])
        self.assertEqual(self.call("post", payload=payload, api=api).data["id"], first.data["id"])
        mismatch = self.call("post", payload={**payload, "audience": "student"}, api=api)
        self.assertEqual(mismatch.status_code, 400)
        logs = NotificationLog.objects.filter(tenant=self.tenant, origin_type="dawon_demo_preview")
        self.assertEqual(logs.count(), 1)
        record = logs.get()
        self.assertEqual(record.amount_deducted, 0)
        self.assertEqual(record.provider_message_id, "")
        self.assertEqual(record.sqs_message_id, "")
        self.assertFalse(record.success)
        self.assertEqual(self.call("get", role="teacher", api=api).status_code, 404)
        self.assertEqual(self.call("get", api=api).data["receipts"][0]["id"], record.pk)

    def test_close_uses_native_snapshot_and_preserves_existing_period(self):
        first = timezone.localdate().replace(day=1)
        previous = first - timedelta(days=1)
        user = get_user_model().objects.create_user(username=EMPLOYEE, tenant=self.tenant, name=FIXTURES[EMPLOYEE][0])
        staff = Staff.objects.create(tenant=self.tenant, user=user, name=user.name, phone="", position="INSTRUCTOR")
        work = WorkType.objects.create(tenant=self.tenant, name=WORK_TYPE, description=WORK_MARK, base_hourly_wage=20000)
        WorkRecord.objects.create(tenant=self.tenant, staff=staff, work_type=work, date=previous,
                                  start_time=time(13), end_date=previous, end_time=time(15), memo=WORK_MARK)
        api = "demo-staff"
        request = {"staff": staff.pk, "year": previous.year, "month": previous.month}
        payroll = {"view": "payroll", "month": previous.strftime("%Y-%m")}
        before = self.call("get", payload=payroll, api=api).data["rows"][0]
        self.assertTrue(before["can_close"])
        result = self.call("post", "month-close/", request, api=api)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(self.call("post", "month-close/", request, api=api).status_code, 200)
        self.assertEqual(WorkMonthLock.objects.filter(staff=staff, is_locked=True).count(), 1)
        snapshot = PayrollSnapshot.objects.get(staff=staff)
        self.assertEqual(snapshot.total_amount, 40000)
        after = self.call("get", payload=payroll, api=api).data["rows"][0]
        self.assertEqual(after["state"], "confirmed")
        self.assertFalse(after["can_close"])
        current = {**request, "year": first.year, "month": first.month}
        self.assertEqual(self.call("post", "month-close/", current, api=api).status_code, 400)
        self.assertEqual(self.call("post", "month-close/", request, role="teacher", api=api).status_code, 404)
