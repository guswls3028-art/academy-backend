from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models.parent_assessment_comment import ParentAssessmentComment
from apps.domains.results.views.parent_assessment_comments_view import (
    AdminParentAssessmentCommentsView, AdminParentAssessmentCommentView, ParentAssessmentCommentsView,
)


Enrollment = apps.get_model("enrollment", "Enrollment")
SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")
Exam = apps.get_model("exams", "Exam")
ExamEnrollment = apps.get_model("exams", "ExamEnrollment")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Parent = apps.get_model("parents", "Parent")
Student = apps.get_model("students", "Student")

class ParentAssessmentCommentTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Feedback", code="feedback", is_active=True)
        User = get_user_model()
        self.staff = User.objects.create_user(username="feedback-staff", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        self.parent_user = User.objects.create_user(username="feedback-parent", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.parent = Parent.objects.create(tenant=self.tenant, user=self.parent_user, name="Parent", phone="01011112222")
        self.student_user = User.objects.create_user(username="feedback-student", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.student = Student.objects.create(tenant=self.tenant, user=self.student_user, parent=self.parent,
                                              name="Child", ps_number="FB001", omr_code="00000001", parent_phone="01011112222")
        self.lecture = Lecture.objects.create(tenant=self.tenant, title="Feedback", name="Feedback", subject="SCIENCE")
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="Session")
        self.enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=self.lecture)
        SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        self.exam = Exam.objects.create(tenant=self.tenant, title="Exam")
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        self.kwargs = dict(session_id=self.session.id, kind="exam", assessment_id=self.exam.id)

    def write(self, text="학부모 전용", version=0, user=None):
        request = self.factory.put("/", {"text": text, "version": version}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.staff)
        return AdminParentAssessmentCommentView.as_view()(request, **self.kwargs, enrollment_id=self.enrollment.id)

    def parent_read(self, user=None, child=None):
        request = self.factory.get("/", HTTP_X_STUDENT_ID=str(child or self.student.id))
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.parent_user)
        return ParentAssessmentCommentsView.as_view()(request)

    def test_write_reload_parent_read_and_history(self):
        self.assertEqual(self.write().status_code, 200)
        self.assertEqual(self.write("수정", 1).status_code, 200)
        comment = ParentAssessmentComment.objects.get()
        self.assertEqual(comment.version, 2)
        self.assertEqual(list(comment.revisions.values_list("text", flat=True)), ["수정", "학부모 전용"])
        item = self.parent_read().data["items"][0]
        self.assertEqual(item["text"], "수정")
        self.assertEqual(item["assessment_title"], "Exam")
        self.assertEqual(item["session_title"], "Session")
        self.assertEqual(item["lecture_title"], "Feedback")
        self.assertEqual(self.write(" \n ", 2).status_code, 200)
        self.assertEqual(self.parent_read().data["items"], [])

    def test_stale_edit_preserves_previous_text(self):
        self.write()
        self.assertEqual(self.write("stale", 0).status_code, 409)
        self.assertEqual(ParentAssessmentComment.objects.get().text, "학부모 전용")

    def test_students_cannot_read_or_write(self):
        self.write()
        self.assertEqual(self.parent_read(self.student_user).status_code, 403)
        self.assertEqual(self.write(user=self.student_user).status_code, 403)

    def test_unassigned_and_removed_targets_are_not_published(self):
        self.write()
        SessionEnrollment.objects.all().delete()
        self.assertEqual(self.parent_read().data["items"], [])
        self.assertEqual(self.write("bad", 1).status_code, 404)
        self.exam.sessions.clear()
        self.assertEqual(self.parent_read().data["items"], [])

    def test_length_and_wrong_child_fail_closed(self):
        self.assertEqual(self.write("x" * 2001).status_code, 400)
        self.assertEqual(self.parent_read(child=self.student.id + 10000).status_code, 403)
        request = self.factory.get("/")
        request.tenant = Tenant.objects.create(name="Other", code="feedback-other")
        force_authenticate(request, user=self.staff)
        self.assertEqual(AdminParentAssessmentCommentsView.as_view()(request, **self.kwargs).status_code, 403)

    def test_default_exam_target_before_scores_and_ended_lecture_history(self):
        ExamEnrollment.objects.all().delete()
        self.assertEqual(self.write().status_code, 200)
        self.lecture.is_active = False
        self.lecture.save(update_fields=["is_active"])
        self.assertEqual(self.parent_read().data["items"][0]["text"], "학부모 전용")
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status"])
        self.assertEqual(self.parent_read().data["items"], [])

    def test_homework_comment_requires_exact_assignment_and_is_hidden_when_removed(self):
        Homework = apps.get_model("homework_results", "Homework")
        HomeworkAssignment = apps.get_model("homework", "HomeworkAssignment")
        homework = Homework.objects.create(tenant=self.tenant, session=self.session, title="숙제")
        self.kwargs.update(kind="homework", assessment_id=homework.id)
        self.assertEqual(self.write().status_code, 404)
        HomeworkAssignment.objects.create(tenant=self.tenant, session=self.session,
                                           homework=homework, enrollment=self.enrollment)
        self.assertEqual(self.write().status_code, 200)
        item = self.parent_read().data["items"][0]
        self.assertEqual(item["assessment_title"], "숙제")
        homework.meta = {"removed_from_session_at": "2026-10-19T10:00:00Z"}
        homework.save(update_fields=["meta"])
        self.assertEqual(self.parent_read().data["items"], [])
