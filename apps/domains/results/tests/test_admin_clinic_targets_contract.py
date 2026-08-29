import datetime

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.views.admin_clinic_targets_view import (
    AdminClinicTargetsView,
)


User = get_user_model()


class AdminClinicTargetsContractTests(TestCase):
    def setUp(self):
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.Exam = django_apps.get_model("exams", "Exam")
        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.LectureSession = django_apps.get_model("lectures", "Session")
        self.ClinicSession = django_apps.get_model("clinic", "Session")
        self.SessionParticipant = django_apps.get_model(
            "clinic", "SessionParticipant"
        )
        self.SessionParticipantPlanItem = django_apps.get_model(
            "clinic", "SessionParticipantPlanItem"
        )
        self.ClinicLink = django_apps.get_model("progress", "ClinicLink")
        self.Student = django_apps.get_model("students", "Student")
        self.factory = APIRequestFactory()

        self.tenant = Tenant.objects.create(
            name="Clinic projection tenant",
            code="clinic-projection",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="clinic_projection_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        student_user = User.objects.create_user(
            username="clinic_projection_student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = self.Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="PROJ001",
            omr_code="PROJ001",
            name="연결 학생",
            parent_phone="01000000000",
        )
        self.lecture = self.Lecture.objects.create(
            tenant=self.tenant,
            title="연결 수학",
            name="연결 수학",
            subject="MATH",
        )
        self.lecture_session = self.LectureSession.objects.create(
            lecture=self.lecture,
            order=1,
            title="연결 차시",
        )
        self.enrollment = self.Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="연결 시험",
            pass_score=80,
            max_score=100,
        )
        self.exam.sessions.add(self.lecture_session)
        self.attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            attempt_index=1,
            status="done",
            meta={"total_score": 40},
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=self.attempt,
            total_score=40,
            max_score=100,
        )
        self.link = self.ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lecture_session,
            reason="AUTO_FAILED",
            source_type="exam",
            source_id=self.exam.id,
            is_auto=True,
            resolution_evidence={"score": 40, "pass_score": 80},
            resolution_history=[
                {
                    "at": "2026-08-29T09:00:00+09:00",
                    "action": "unresolve",
                    "resolution_type": "EXAM_PASS",
                    "evidence": {"score": 40},
                }
            ],
        )

    def _get(self, *, tenant_marker=True, user=None):
        request = self.factory.get("/results/admin/clinic-targets/")
        if tenant_marker:
            request.tenant = self.tenant
        force_authenticate(request, user=user or self.admin)
        return AdminClinicTargetsView.as_view()(request)

    def test_projects_only_authoritative_same_tenant_active_plan_linkage(self):
        clinic_session = self.ClinicSession.objects.create(
            tenant=self.tenant,
            title="토요일 보충",
            date=datetime.date(2026, 8, 29),
            start_time=datetime.time(14, 30),
            duration_minutes=90,
            location="본관 302호",
            max_participants=8,
        )
        participant = self.SessionParticipant.objects.create(
            tenant=self.tenant,
            session=clinic_session,
            student=self.student,
            enrollment=self.enrollment,
            status="booked",
            source="student_request",
            preferred_start_time=datetime.time(15, 0),
            preferred_end_time=datetime.time(16, 0),
            student_request_memo="학원 셔틀 뒤에 도착해요",
            staff_memo="도착하면 3번 좌석 안내",
            memo="노출하면 안 되는 출처 불명 메모",
        )
        plan_item = self.SessionParticipantPlanItem.objects.create(
            participant=participant,
            clinic_link=self.link,
            selected_by=self.admin,
        )

        foreign_tenant = Tenant.objects.create(
            name="Foreign clinic tenant",
            code="foreign-clinic-projection",
            is_active=True,
        )
        foreign_user = User.objects.create_user(
            username="foreign_clinic_projection_student",
            password="test1234",
            tenant=foreign_tenant,
        )
        foreign_student = self.Student.objects.create(
            tenant=foreign_tenant,
            user=foreign_user,
            ps_number="FOR001",
            omr_code="FOR001",
            name="다른 테넌트 학생",
            parent_phone="01011111111",
        )
        foreign_session = self.ClinicSession.objects.create(
            tenant=foreign_tenant,
            date=datetime.date(2026, 8, 30),
            start_time=datetime.time(9, 0),
            duration_minutes=60,
            location="외부 101호",
            max_participants=8,
        )
        foreign_participant = self.SessionParticipant.objects.create(
            tenant=foreign_tenant,
            session=foreign_session,
            student=foreign_student,
            status="booked",
            source="manual",
            staff_memo="다른 테넌트 메모",
        )
        self.SessionParticipantPlanItem.objects.create(
            participant=foreign_participant,
            clinic_link=self.link,
            selected_by=self.admin,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["resolution_evidence"], {"score": 40, "pass_score": 80})
        self.assertEqual(row["resolution_history"], self.link.resolution_history)
        self.assertEqual(
            row["linked_bookings"],
            [
                {
                    "plan_item_id": plan_item.id,
                    "participant_id": participant.id,
                    "session_id": clinic_session.id,
                    "session_date": "2026-08-29",
                    "session_start_time": "14:30:00",
                    "session_end_time": "16:00:00",
                    "location": "본관 302호",
                    "participant_status": "booked",
                    "preferred_start_time": "15:00:00",
                    "preferred_end_time": "16:00:00",
                    "student_request_memo": "학원 셔틀 뒤에 도착해요",
                    "staff_memo": "도착하면 3번 좌석 안내",
                    "linked_at": serializers.DateTimeField().to_representation(
                        plan_item.created_at
                    ),
                    "linked_by_id": self.admin.id,
                    "linkage_source": "participant_plan",
                }
            ],
        )
        self.assertNotIn("memo", row["linked_bookings"][0])

    def test_missing_tenant_fails_closed_instead_of_empty_success(self):
        before = self.ClinicLink.objects.count()
        request = self.factory.get("/results/admin/clinic-targets/")

        # Exercise the view contract directly so middleware/permissions cannot
        # turn its historic 200 [] fallback into an unrelated denial envelope.
        response = AdminClinicTargetsView().get(request)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(
            response.data,
            {"detail": "Tenant required", "code": "TENANT_REQUIRED"},
        )
        self.assertEqual(self.ClinicLink.objects.count(), before)

    def test_unauthorized_user_is_denied_without_read_mutation(self):
        outsider = User.objects.create_user(
            username="clinic_projection_outsider",
            password="test1234",
        )
        before = self.ClinicLink.objects.count()

        response = self._get(user=outsider)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(self.ClinicLink.objects.count(), before)
