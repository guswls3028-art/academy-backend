"""Cross-domain staff contact visibility and tenant isolation integration tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from types import SimpleNamespace
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.messaging.models import NotificationLog
from apps.domains.messaging.services.contact_projection import current_recipient_phones
from apps.domains.messaging.services.registration_service import _parent_account_target_id
from apps.domains.messaging.views.log_views import NotificationLogDetailView, NotificationLogListView
from apps.domains.students.models import Student
from apps.domains.students.views import StudentViewSet


User = get_user_model()


class StaffContactProjectionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="qa-contact-a", name="Contact A", is_active=True)
        self.other = Tenant.objects.create(code="qa-contact-b", name="Contact B", is_active=True)
        self.student = self.make_student(self.tenant, "S1", "01000000001")
        self.foreign = self.make_student(self.other, "S2", "01000000009")

    def make_student(self, tenant, key, phone):
        user = User.objects.create_user(username=user_internal_username(tenant, key), tenant=tenant)
        return Student.objects.create(
            tenant=tenant, user=user, ps_number=key, name="합성 학생",
            phone=phone, parent_phone="01000000002", omr_code=phone[-8:],
        )

    def staff(self, role, tenant=None):
        tenant = tenant or self.tenant
        user = User.objects.create_user(username=f"{tenant.code}-{role}", tenant=tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def log(self, target_type="student", target_id=None, **kwargs):
        return NotificationLog.objects.create(
            tenant=self.tenant, message_mode="alimtalk", status="sent", success=True,
            target_type=target_type, target_id=str(target_id or self.student.pk),
            recipient_summary="합성 학생 / 010****0001", **kwargs,
        )

    def request(self, user, tenant=None):
        request = self.factory.get("/api/v1/messaging/log/")
        force_authenticate(request, user=user)
        request.tenant = tenant or self.tenant
        return request

    def test_teacher_and_assistant_get_current_contact_without_changing_history_or_body_access(self):
        log = self.log(message_body="보호된 본문", provider_message_id="private-provider-id")
        Student.objects.filter(pk=self.student.pk).update(phone="01000000003")
        for role in ("teacher", "staff"):
            user = self.staff(role)
            for view, kwargs in ((NotificationLogListView, {}), (NotificationLogDetailView, {"pk": log.pk})):
                with self.subTest(role=role, view=view.__name__):
                    response = view.as_view()(self.request(user), **kwargs)
                    self.assertEqual(response.status_code, 200)
                    item = response.data["results"][0] if not kwargs else response.data
                    self.assertEqual(item["current_recipient_phone"], "01000000003")
                    self.assertEqual(item["recipient_summary"], "합성 학생 / 010****0001")
                    self.assertEqual(item["message_body"], "")
                    self.assertEqual(item["provider_message_id"], "")
        log.refresh_from_db()
        self.assertEqual(log.recipient_summary, "합성 학생 / 010****0001")

    def test_cross_tenant_reference_does_not_resolve_and_foreign_detail_is_hidden(self):
        forged = self.log(target_id=self.foreign.pk)
        self.assertEqual(current_recipient_phones(self.tenant, [forged]), {})
        teacher = self.staff("teacher", self.other)
        response = NotificationLogListView.as_view()(self.request(teacher, self.other))
        self.assertEqual(response.data["results"], [])
        detail = NotificationLogDetailView.as_view()(self.request(teacher, self.other), pk=forged.pk)
        self.assertEqual(detail.status_code, 404)
        denied = NotificationLogListView.as_view()(self.request(teacher, self.tenant))
        self.assertEqual(denied.status_code, 403)

    def test_common_provider_tenant_does_not_gain_business_contacts(self):
        log = self.log(source_tenant=self.other, target_id=self.foreign.pk)
        self.assertEqual(current_recipient_phones(self.tenant, [log]), {})
        self.assertEqual(current_recipient_phones(self.other, [log]), {log.pk: "01000000009"})
        response = NotificationLogDetailView.as_view()(self.request(self.staff("owner")), pk=log.pk)
        self.assertEqual(response.status_code, 404)

    def test_exact_student_and_parent_keys_resolve_in_one_query(self):
        logs = [self.log(), self.log("parent")]
        with self.assertNumQueries(1):
            contacts = current_recipient_phones(self.tenant, logs)
        self.assertEqual(contacts, {logs[0].pk: "01000000001", logs[1].pk: "01000000002"})
        Student.objects.filter(pk=self.student.pk).update(uses_identifier=True)
        self.assertEqual(current_recipient_phones(self.tenant, logs)[logs[0].pk], "")
        Student.objects.filter(pk=self.student.pk).update(deleted_at=timezone.now())
        self.assertEqual(current_recipient_phones(self.tenant, logs), {})

    def test_unknown_references_and_nonstaff_do_not_expose_contacts(self):
        logs = [self.log("account", "unknown"), self.log("account", f"parent-account:{self.student.pk}"), self.log("student", "01000000001")]
        self.assertEqual(current_recipient_phones(self.tenant, logs), {})
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student.user, role="student")
        response = NotificationLogListView.as_view()(self.request(self.student.user))
        self.assertEqual(response.status_code, 403)

    def test_numeric_legacy_account_fallback_does_not_resolve_to_another_family(self):
        legacy_target = _parent_account_target_id(
            SimpleNamespace(ps_number=str(self.student.pk)), "01000000008",
        )
        self.assertEqual(legacy_target, f"parent:{self.student.pk}")
        logs = [self.log("account", legacy_target), self.log("account", f"student:{self.student.pk}")]
        with self.assertNumQueries(0):
            self.assertEqual(current_recipient_phones(self.tenant, logs), {})

    def test_student_list_search_and_detail_preserve_teacher_assistant_tenant_boundary(self):
        for role in ("teacher", "staff"):
            user = self.staff(role)
            for query in ({}, {"search": "합성"}, {"search": self.foreign.phone}):
                with self.subTest(role=role, query=query):
                    request = self.factory.get("/api/v1/students/", query)
                    force_authenticate(request, user=user)
                    request.tenant = self.tenant
                    response = StudentViewSet.as_view({"get": "list"})(request)
                    self.assertEqual(response.status_code, 200)
                    rows = response.data["results"]
                    self.assertNotIn(self.foreign.pk, [row["id"] for row in rows])
                    if not query or query == {"search": "합성"}:
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0]["phone"], self.student.phone)
                        self.assertEqual(rows[0]["parent_phone"], self.student.parent_phone)
            for target, expected in ((self.student, 200), (self.foreign, 404)):
                response = StudentViewSet.as_view({"get": "retrieve"})(self.request(user), pk=target.pk)
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    self.assertEqual(response.data["phone"], self.student.phone)
                    self.assertEqual(response.data["parent_phone"], self.student.parent_phone)
