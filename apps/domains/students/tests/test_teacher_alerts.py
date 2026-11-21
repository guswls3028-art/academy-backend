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

    def test_recent_and_top_reference_thresholds_are_applied_to_same_course(self):
        peer = Student.objects.create(tenant=self.tenant,user=self.user("student","classmate"),
                                      name="같은 반 학생",ps_number="002",omr_code="00000002")
        peer_row = Enrollment.objects.create(tenant=self.tenant,lecture=self.lecture,student=peer,status="ACTIVE")
        for index in range(4):
            if index < 2:
                session = self.sessions[index]
            else:
                session = Session.objects.create(lecture=self.lecture,order=index+1,title=str(index+1),date=date(2020,1,index+1))
                SessionEnrollment.objects.create(tenant=self.tenant,session=session,enrollment=self.enrollment)
            SessionEnrollment.objects.create(tenant=self.tenant,session=session,enrollment=peer_row)
            exam = Exam.objects.create(tenant=self.tenant,title=f"exam{index}",max_score=100)
            exam.sessions.add(session)
            for enrollment, score in [(self.enrollment, 50 if index==3 else 90),(peer_row,100)]:
                ExamEnrollment.objects.create(exam=exam,enrollment=enrollment)
                Result.objects.create(target_type="exam",target_id=exam.pk,enrollment=enrollment,total_score=score,max_score=100)
        evidence = self.alerts()
        self.assertEqual(evidence["recent_exam_drop"],40)
        self.assertEqual(evidence["top_reference_gap"],50)
        response = self.call("patch",{"rules":{"recent_exam_drop":{"threshold":41},"top_reference_gap":{"threshold":51}}})
        self.assertEqual(response.status_code,200)
        self.assertFalse({"recent_exam_drop","top_reference_gap"}.intersection(self.alerts()))
        self.assertEqual(self.call(actor=self.user("teacher","other-teacher")).data["alerts"],[])

    def test_staff_queue_persists_one_occurrence_and_reopens_across_roles(self):
        for session in self.sessions:
            Attendance.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment, status="ABSENT")
        director = self.user("admin")
        other = self.user("teacher", "other")
        def feed(actor, status="pending", **filters):
            return self.call("get", {"queue":"list", "status":status, **filters}, actor=actor).data
        def summary(actor):
            return self.call("get", {"queue":"summary"}, actor=actor).data
        key = feed(self.teacher)["alerts"][0]["key"]
        payload = {"key":key, "status":"completed"}
        self.assertEqual(self.call("patch",payload, actor=other).status_code, 404)
        self.assertEqual(summary(other)["pending"], 0)
        self.assertEqual(self.call("patch",payload, actor=director).status_code, 204)
        first = feed(self.teacher, "completed")["alerts"][0]
        self.assertEqual(self.call("patch",payload, actor=self.teacher).status_code, 204)
        self.assertEqual(first["completed_at"], feed(director, "completed")["alerts"][0]["completed_at"])
        self.assertEqual(summary(self.teacher)["pending"], 0)
        self.assertEqual(feed(director, "completed", rule="exam_drop")["count"], 0)
        self.assertEqual(feed(director, "completed", q="학생")["count"], 1)
        self.assertEqual(feed(other, "completed")["count"], 0)
        third = Session.objects.create(lecture=self.lecture, order=3, title="3", date=date(2020,1,3))
        SessionEnrollment.objects.create(tenant=self.tenant,enrollment=self.enrollment,session=third)
        Attendance.objects.create(tenant=self.tenant,session=third,enrollment=self.enrollment,status="ABSENT")
        self.assertNotEqual(feed(self.teacher)["alerts"][0]["key"], key)
        self.assertEqual(summary(director)["pending"], 1)
        self.assertEqual(self.call("patch",{**payload,"status":"pending"},actor=self.teacher).status_code, 204)
        self.assertEqual(summary(director)["pending"], 2)
        self.assertEqual(feed(director,"completed")["count"], 0)
