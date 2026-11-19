from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.clinic.dawon_demo import allows_demo_clinic_write, ensure_demo_clinic_sessions
from apps.domains.clinic.models import Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture
from apps.domains.students.models import Student


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonClinicTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원", clinic_auto_approve_booking=True)
        self.users = {}
        for role in ("student", "teacher"):
            user = get_user_model().objects.create_user(username=role, tenant=self.tenant)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
            self.users[role] = user
        student = Student.objects.create(id=2, tenant=self.tenant, user=self.users["student"], name="다원 수업 검수 학생 2",
                                         is_managed=True, ps_number="qa-sample", omr_code="00000002")
        lecture = Lecture.objects.create(tenant=self.tenant, title="수학", subject="수학")
        Enrollment.objects.create(tenant=self.tenant, student=student, lecture=lecture)
        ensure_demo_clinic_sessions(self.tenant)
        self.session = Session.objects.order_by("date").first()

    def call(self, method, path, data, role):
        path = "/api/v1/clinic/" + path
        req = getattr(APIRequestFactory(), method)(path, data, format="json")
        req.tenant = self.tenant
        if method != "get":
            self.assertTrue(allows_demo_clinic_write(SimpleNamespace(method=method.upper(), path=path, data=data,
                                                                     tenant=self.tenant), role))
        force_authenticate(req, user=self.users[role], token={"dawon_demo_role": role})
        match = resolve(path)
        return match.func(req, **match.kwargs)

    def test_native_request_approval_and_student_readback(self):
        ensure_demo_clinic_sessions(self.tenant)
        self.assertEqual(Session.objects.count(), 7)
        with patch("apps.domains.clinic.views.participant_views.send_clinic_event_notification") as send:
            with self.captureOnCommitCallbacks(execute=True):
                created = self.call("post", "participants/bulk-create/", {"session_ids": [self.session.pk]}, "student")
            self.assertEqual(created.status_code, 201, created.data)
            row = created.data["participants"][0]
            self.assertEqual(row["status"], "pending")
            path = f"participants/{row['id']}/"
            approved = self.call("patch", path + "set_status/", {"status": "booked"}, "teacher")
            self.assertEqual(approved.status_code, 200, approved.data)
            self.assertEqual(self.call("get", path, {}, "student").data["status"], "booked")
            cancelled = self.call("patch", path + "set_status/", {"status": "cancelled"}, "student")
            self.assertEqual(cancelled.status_code, 200, cancelled.data)
            send.assert_not_called()

    def test_demo_writes_reject_unmarked_sessions_and_extra_identity_fields(self):
        request = SimpleNamespace(method="POST", path="/api/v1/clinic/participants/bulk-create/",
                                  data={"session_ids": [self.session.pk]}, tenant=self.tenant)
        self.assertFalse(allows_demo_clinic_write(request, "teacher"))
        request.data["student_id"] = 3
        self.assertFalse(allows_demo_clinic_write(request, "student"))
        request.data.pop("student_id")
        request.data["student_request_memo"] = "개인 메모"
        self.assertFalse(allows_demo_clinic_write(request, "student"))
        request.data.pop("student_request_memo")
        self.session.memo = ""
        self.session.save(update_fields=["memo"])
        self.assertFalse(allows_demo_clinic_write(request, "student"))
