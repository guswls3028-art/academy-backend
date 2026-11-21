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
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonClinicTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원", clinic_auto_approve_booking=True)
        self.users = {}
        for role in ("student", "teacher", "admin"):
            user = get_user_model().objects.create_user(username="t719_dawon-demo-"+role, tenant=self.tenant)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
            self.users[role] = user
        student = Student.objects.create(id=2, tenant=self.tenant, user=self.users["student"], name="다원 수업 검수 학생 2",
                                         is_managed=True, ps_number="qa-sample", omr_code="00000002")
        lecture = Lecture.objects.create(tenant=self.tenant, title="수학", subject="수학")
        self.group = ClassGroup.objects.create(tenant=self.tenant,name="담당 반",kind="union",track="SKY",assigned_teacher=self.users["teacher"])
        self.group.students.add(student)
        ClassGroupLecture.objects.create(group=self.group,lecture=lecture,period="vacation")
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

    def clinic_directory(self, role, **scope):
        request=APIRequestFactory().get("/api/v1/students/demo-operations/", {"view":"care","kind":"clinic",**scope})
        request.tenant=self.tenant
        force_authenticate(request,user=self.users[role],token={"dawon_demo_role":role})
        return resolve(request.path).func(request)

    def test_director_approval_and_teacher_scope(self):
        from apps.domains.clinic.models import SessionParticipant
        from apps.core.authentication import _validate_dawon_demo_access
        from django.utils import timezone
        posted=self.call("post","participants/bulk-create/",{"session_ids":[self.session.pk]},"student")
        self.assertEqual(posted.status_code,201,posted.data)
        pk=posted.data["participants"][0]["id"]
        self.assertEqual(self.clinic_directory("teacher",status="pending",group=self.group.pk).data["count"],1)
        outside=ClassGroup.objects.create(tenant=self.tenant,name="담당 밖",kind="union",track="HYPER")
        self.assertEqual(self.clinic_directory("teacher",group=outside.pk).status_code,404)
        self.assertEqual(self.clinic_directory("teacher",kind="requests").status_code,404)
        self.assertEqual(self.clinic_directory("teacher",view="learners").status_code,404)
        self.assertEqual(self.clinic_directory("admin",record=pk).data["rows"][0]["can_review"],True)
        route=f"participants/{pk}/set_status/"
        native=SimpleNamespace(tenant=self.tenant,method="PATCH",path="/api/v1/clinic/"+route,data={"status":"rejected"})
        _validate_dawon_demo_access(native,{"dawon_demo_role":"admin","dawon_demo_until":int(timezone.now().timestamp())+60})
        result=self.call("patch",route,{"status":"rejected"},"admin")
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.call("get",f"participants/{pk}/",{},"student").data["status"],"rejected")
        self.assertEqual(self.clinic_directory("admin",status="pending").data["count"],0)
        self.assertFalse(allows_demo_clinic_write(native,"admin"))
        from apps.domains.clinic.services.lifecycle import change_participant_status, Conflict
        with self.assertRaises(Conflict):
            change_participant_status(tenant=self.tenant,participant_id=pk,next_status="booked",actor=self.users["teacher"],expected_status="pending")
        self.assertEqual(self.clinic_directory("teacher",status="rejected").data["count"],1)
        self.group.assigned_teacher=None
        self.group.save(update_fields=["assigned_teacher"])
        self.assertEqual(self.clinic_directory("teacher",status="rejected").data["count"],0)
        self.assertEqual(self.call("get",f"participants/{pk}/",{},"teacher").status_code,404)
        self.assertEqual(SessionParticipant.objects.get(pk=pk).status,"rejected")

    def test_staff_share_clinic_attendance_and_student_sees_the_corrected_status(self):
        from apps.core.authentication import _validate_dawon_demo_access
        from django.utils import timezone

        token = {"dawon_demo_role": "admin", "dawon_demo_until": int(timezone.now().timestamp()) + 60}
        for endpoint in ("sessions/tree/", "participants/"):
            _validate_dawon_demo_access(SimpleNamespace(tenant=self.tenant, method="GET",
                path="/api/v1/clinic/" + endpoint), token)
        booked = self.call("post", "participants/bulk-create/", {"session_ids": [self.session.pk]}, "student")
        pk = booked.data["participants"][0]["id"]
        path = f"participants/{pk}/"
        with patch("apps.domains.clinic.views.participant_views.send_clinic_event_notification") as send:
            self.assertEqual(self.call("patch", path+"set_status/", {"status": "booked"}, "teacher").status_code, 200)
            self.assertTrue(self.clinic_directory("admin", record=pk).data["rows"][0]["can_attend"])
            arrival = self.call("patch", path+"set_status/", {"status": "attended", "is_late": True}, "admin")
            self.assertEqual(arrival.status_code, 200, arrival.data)
            observed = self.call("get", path, {}, "student").data
            self.assertEqual(observed["status"], "attended")
            self.assertTrue(observed["is_late"])
            stale = self.call("patch", path+"set_status/", {"status": "booked",
                "expected_checked_in_at": "2020-01-01T00:00:00Z"}, "teacher")
            self.assertEqual(stale.status_code, 409, stale.data)
            restored = self.call("patch", path+"set_status/", {"status": "booked",
                "expected_checked_in_at": observed["checked_in_at"]}, "teacher")
            self.assertEqual(restored.status_code, 200, restored.data)
            absent = self.call("patch", path+"set_status/", {"status": "no_show"}, "admin")
            self.assertEqual(absent.status_code, 200, absent.data)
            self.assertEqual(self.call("get", path, {}, "student").data["status"], "no_show")
            self.assertFalse(self.clinic_directory("teacher", record=pk).data["rows"][0]["can_attend"])
            undo = self.call("patch", path+"set_status/", {"status": "booked", "expected_status": "no_show"}, "admin")
            self.assertEqual(undo.status_code, 200, undo.data)
            self.assertEqual(self.call("get", path, {}, "student").data["status"], "booked")
            send.assert_not_called()
