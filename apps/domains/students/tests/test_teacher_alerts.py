from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.domains.students.views.teacher_alerts import DawonTeacherAlertsView
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoProgress


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class TeacherAlertsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.teacher = self.user("teacher")
        pupil = self.user("student")
        student = Student.objects.create(tenant=self.tenant, user=pupil, name="학생", ps_number="001", omr_code="00000001")
        self.lecture = Lecture.objects.create(tenant=self.tenant, title="강의", name="teacher", subject="수학")
        group = ClassGroup.objects.create(tenant=self.tenant, name="반", kind="union", track="SKY")
        ClassGroupLecture.objects.create(group=group, lecture=self.lecture, period="vacation", reference_teacher=self.teacher)
        self.enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=self.lecture, student=student, status="ACTIVE")
        self.sessions = []
        for index in (1, 2):
            session = Session.objects.create(lecture=self.lecture, order=index, title=str(index), date=date(2020, 1, index))
            SessionEnrollment.objects.create(tenant=self.tenant, enrollment=self.enrollment, session=session)
            self.sessions.append(session)

    def user(self, role, name=None):
        user = get_user_model().objects.create_user(tenant=self.tenant, username=name or role)
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        return user

    def call(self, method="get", payload=None, *, actor=None, tenant=None):
        req = getattr(APIRequestFactory(), method)("/api/v1/students/teacher-alerts/", payload or {}, format="json")
        req.tenant = tenant or self.tenant
        force_authenticate(req, user=actor or self.teacher)
        return DawonTeacherAlertsView.as_view()(req)

    def alerts(self):
        response = self.call()
        self.assertEqual(response.status_code, 200, response.data)
        return {item["rule"]: item["value"] for item in response.data["alerts"]}

    def test_absence_requires_consecutive_explicit_marks_and_settings_persist(self):
        for session in self.sessions:
            Attendance.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment, status="ABSENT")
        self.assertEqual(self.alerts(), {"absence_streak": 2})
        response = self.call("patch", {"rules": {"absence_streak": {"threshold": 3}}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.alerts(), {})
        self.assertEqual(self.call("patch", {"rules": {"absence_streak": {"threshold": 0}}}).status_code, 400)
        self.assertEqual(self.call().data["rules"]["absence_streak"]["threshold"], 3)
        self.call("patch", {"rules": {"absence_streak": {"threshold": 2}}})
        Attendance.objects.filter(session=self.sessions[-1]).update(status="UNSET")
        self.assertEqual(self.alerts(), {})
        other = self.user("teacher", "other")
        self.assertEqual(self.call(actor=other).data["lectures"], [])
        self.assertEqual(self.call(payload={"lecture_id": self.lecture.pk}, actor=other).status_code, 404)
        self.assertEqual(self.call(actor=self.enrollment.student.user).status_code, 403)
        other_tenant = Tenant.objects.create(code="other", name="격리")
        TenantMembership.ensure_active(tenant=other_tenant, user=self.teacher, role="teacher")
        self.assertEqual(self.call(tenant=other_tenant).status_code, 404)

    def test_homework_and_exam_rates_and_overdue_only(self):
        for index, session in enumerate(self.sessions):
            exam = Exam.objects.create(tenant=self.tenant, title=f"exam{index}", max_score=100)
            exam.sessions.add(session)
            ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
            Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=self.enrollment,
                                  total_score=[10, 18][index], max_score=20)
            hw = Homework.objects.create(tenant=self.tenant, session=session, title=f"hw{index}")
            HomeworkAssignment.objects.create(tenant=self.tenant, session=session, homework=hw, enrollment=self.enrollment)
            HomeworkScore.objects.create(homework=hw, session=session, enrollment=self.enrollment, score=[19, 8][index], max_score=20)
            HomeworkScore.objects.create(homework=hw, session=session, enrollment=self.enrollment,
                                         score=20, max_score=20, attempt_index=2)
            missing = Homework.objects.create(tenant=self.tenant, session=session, title=f"missing{index}",
                grading_mode="COMPLETION", meta={"due_date":"2020-01-02"})
            HomeworkAssignment.objects.create(tenant=self.tenant, session=session, homework=missing, enrollment=self.enrollment)
        self.assertEqual(self.alerts(), {"exam_rise":40, "homework_drop":55, "homework_missing":2})
        Homework.objects.filter(pk=missing.pk).update(meta={"due_date": "invalid"})
        self.assertNotIn("homework_missing", self.alerts())

    def test_video_requires_assigned_playable_monitored_replacement(self):
        session = self.sessions[0]
        mark = Attendance.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment, status="PRESENT")
        video = Video.objects.create(tenant=self.tenant, session=session, title="대체수업", status="READY", hls_path="a/index.m3u8")
        Video.objects.create(tenant=self.tenant, session=session, order=2, title="인코딩 대기")
        state = VideoProgress.objects.create(video=video, enrollment=self.enrollment, progress=.25)
        self.assertEqual(self.alerts(), {})
        mark.status = "ONLINE"
        mark.save()
        self.assertEqual(self.alerts(), {"video_watch":25})
        self.assertEqual(self.call("patch", {"rules":{"video_watch":{"threshold":25}}}).data["alerts"], [])
        self.call("patch", {"rules":{"video_watch":{"threshold":80}}})
        perm = VideoAccess.objects.create(video=video, enrollment=self.enrollment, access_mode=AccessMode.BLOCKED, is_override=True)
        self.assertEqual(self.alerts(), {})
        perm.delete()
        state.completed = True
        state.save()
        self.assertEqual(self.alerts(), {})
        state.delete()
        self.assertEqual(self.alerts(), {"video_watch":0})
        SessionEnrollment.objects.filter(session=session).delete()
        self.assertEqual(self.alerts(), {})
