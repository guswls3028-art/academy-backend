from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.lectures.views import LectureViewSet, SessionViewSet
from apps.domains.progress.models import ClinicLink, SessionProgress
from apps.domains.results.models import ScoreEditDraft
from apps.domains.students.models import Student
from apps.domains.video.models import Video


User = get_user_model()


class LectureSessionDeleteGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Lecture Delete Guard",
            code="lecture-delete-guard",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="lecture-delete-guard-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self._seq = 0

    def _create_lecture(self, suffix: str) -> Lecture:
        return Lecture.objects.create(
            tenant=self.tenant,
            title=f"Delete Guard Lecture {suffix}",
            name=f"Delete Guard Lecture {suffix}",
            subject="MATH",
        )

    def _create_session(self, lecture: Lecture, suffix: str) -> Session:
        self._seq += 1
        return Session.objects.create(
            lecture=lecture,
            order=self._seq,
            title=f"Session {suffix}",
        )

    def _create_enrollment(self, lecture: Lecture, suffix: str) -> Enrollment:
        self._seq += 1
        user = User.objects.create_user(
            username=f"lecture-delete-guard-student-{self._seq}",
            password="pw1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Delete Guard Student {suffix}",
            ps_number=f"LDG-{self._seq:04d}",
            omr_code=f"{self._seq:08d}",
            parent_phone="01000000000",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )

    def _delete_lecture(self, lecture: Lecture):
        request = self.factory.delete(f"/api/v1/lectures/lectures/{lecture.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return LectureViewSet.as_view({"delete": "destroy"})(
            request,
            pk=lecture.id,
        )

    def _delete_session(self, session: Session):
        request = self.factory.delete(f"/api/v1/lectures/sessions/{session.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return SessionViewSet.as_view({"delete": "destroy"})(
            request,
            pk=session.id,
        )

    def test_empty_lecture_and_session_remain_deletable(self):
        lecture = self._create_lecture("empty")
        session = self._create_session(lecture, "empty")

        session_response = self._delete_session(session)
        lecture_response = self._delete_lecture(lecture)

        self.assertEqual(session_response.status_code, 204)
        self.assertEqual(lecture_response.status_code, 204)
        self.assertFalse(Session.objects.filter(id=session.id).exists())
        self.assertFalse(Lecture.objects.filter(id=lecture.id).exists())

    def test_lecture_with_enrollment_cannot_be_deleted(self):
        lecture = self._create_lecture("enrolled")
        self._create_enrollment(lecture, "enrolled")

        response = self._delete_lecture(lecture)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Lecture.objects.filter(id=lecture.id).exists())

    def test_lecture_with_used_session_cannot_be_deleted(self):
        lecture = self._create_lecture("video")
        session = self._create_session(lecture, "video")
        Video.objects.create(
            tenant=self.tenant,
            session=session,
            title="Operational Video",
        )

        response = self._delete_lecture(lecture)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Lecture.objects.filter(id=lecture.id).exists())
        self.assertTrue(Session.objects.filter(id=session.id).exists())

    def test_session_with_operational_records_cannot_be_deleted(self):
        blockers = (
            (
                "session enrollment",
                lambda session, enrollment: SessionEnrollment.objects.create(
                    tenant=self.tenant,
                    session=session,
                    enrollment=enrollment,
                ),
            ),
            (
                "attendance",
                lambda session, enrollment: Attendance.objects.create(
                    tenant=self.tenant,
                    session=session,
                    enrollment=enrollment,
                    status="PRESENT",
                ),
            ),
            (
                "exam",
                lambda session, enrollment: Exam.objects.create(
                    tenant=self.tenant,
                    title=f"Exam {session.id}",
                    exam_type=Exam.ExamType.REGULAR,
                    max_score=100,
                    pass_score=60,
                ).sessions.add(session),
            ),
            (
                "homework",
                lambda session, enrollment: Homework.objects.create(
                    tenant=self.tenant,
                    session=session,
                    title=f"Homework {session.id}",
                ),
            ),
            (
                "session progress",
                lambda session, enrollment: SessionProgress.objects.create(
                    session=session,
                    enrollment=enrollment,
                ),
            ),
            (
                "clinic link",
                lambda session, enrollment: ClinicLink.objects.create(
                    tenant=self.tenant,
                    session=session,
                    enrollment=enrollment,
                    reason=ClinicLink.Reason.AUTO_FAILED,
                    is_auto=True,
                ),
            ),
            (
                "video",
                lambda session, enrollment: Video.objects.create(
                    tenant=self.tenant,
                    session=session,
                    title=f"Video {session.id}",
                ),
            ),
            (
                "score edit draft",
                lambda session, enrollment: ScoreEditDraft.objects.create(
                    tenant_id=self.tenant.id,
                    session_id=session.id,
                    editor_user_id=self.admin.id,
                    payload=[],
                ),
            ),
        )

        for label, create_child in blockers:
            with self.subTest(label=label):
                lecture = self._create_lecture(label)
                session = self._create_session(lecture, label)
                enrollment = self._create_enrollment(lecture, label)
                create_child(session, enrollment)

                response = self._delete_session(session)

                self.assertEqual(response.status_code, 403, response.data)
                self.assertTrue(Session.objects.filter(id=session.id).exists())
