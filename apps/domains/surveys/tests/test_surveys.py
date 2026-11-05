from datetime import timedelta
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.support.students.lifecycle import permanently_delete_students, soft_delete_student

from apps.domains.surveys import views
from apps.domains.surveys.models import Survey, SurveyRecipient, SurveyResponse

InventoryFile = apps.get_model("inventory", "InventoryFile")
Parent = apps.get_model("parents", "Parent")
StudentReportedScore = apps.get_model("results", "StudentReportedScore")
Student = apps.get_model("students", "Student")

class SurveyJourneyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="survey-test", name="설문 학원", is_active=True)
        self.other_tenant = Tenant.objects.create(code="survey-other", name="다른 학원", is_active=True)
        self.teacher = self.user("teacher", "teacher")
        self.student = self.make_student("one")
        self.second = self.make_student("two")
        self.foreign = self.make_student("foreign", self.other_tenant)
        self.parent_user = self.user("parent", "parent")
        self.parent = Parent.objects.create(tenant=self.tenant, user=self.parent_user, name="학부모", phone="01011112222")
        self.student.parent = self.parent
        self.student.save(update_fields=["parent"])

    def user(self, label, role, tenant=None):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(username=f"survey-{label}", password="test", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def make_student(self, label, tenant=None):
        tenant = tenant or self.tenant
        return Student.objects.create(tenant=tenant, user=self.user(label, "student", tenant), name=label, ps_number=f"SURVEY-{label}", omr_code=label.ljust(8, "0")[:8])

    def call(self, view, method="get", data=None, user=None, tenant=None, child=None, **kwargs):
        request = getattr(self.factory, method)("/api/v1/surveys/", data=data or {}, format="json", **({"HTTP_X_STUDENT_ID": str(child)} if child is not None else {}))
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.teacher)
        return view.as_view()(request, **kwargs)

    def draft(self, **values):
        data = {"title": "보강 시간 조사", "kind": "general", "audience": {"mode": "all"}, "questions": [{"id": "time", "label": "시간", "type": "single_choice", "required": True, "options": ["토요일", "일요일"]}], **values}
        response = self.call(views.SurveyListView, "post", data)
        self.assertEqual(response.status_code, 201, response.data)
        return Survey.objects.get(id=response.data["id"])

    def publish(self, survey):
        result = self.call(views.SurveyPublishView, "post", {"expected_version": survey.version}, survey_id=survey.id)
        self.assertEqual(result.status_code, 200, result.data)
        survey.refresh_from_db()
        return survey

    def submit(self, survey, student=None, **values):
        student = student or self.student
        return self.call(views.StudentSurveyResponseView, "put", {"answers": {"time": "토요일"}, "expected_version": 0, **values}, user=student.user, tenant=student.tenant, survey_id=survey.id)

    def file(self, student=None):
        student = student or self.student
        return InventoryFile.objects.create(tenant=student.tenant, scope="student", student_ps=student.ps_number, display_name="성적표.pdf", original_name="성적표.pdf", r2_key=f"tenants/{student.tenant_id}/survey/{uuid4()}.pdf", size_bytes=40, content_type="application/pdf")

    def test_create_retry_and_publish_snapshot_and_frozen_structure(self):
        key = str(uuid4())
        survey = self.draft(creation_key=key)
        retry = self.call(views.SurveyListView, "post", {"title": survey.title, "kind": survey.kind, "audience": survey.audience, "questions": survey.questions, "creation_key": key})
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data["id"], survey.id)
        self.publish(survey)
        self.make_student("later")
        self.assertEqual(set(SurveyRecipient.objects.filter(survey=survey).values_list("student_id", flat=True)), {self.student.id, self.second.id})
        result = self.call(views.SurveyDetailView, "patch", {"expected_version": survey.version, "questions": []}, survey_id=survey.id)
        self.assertEqual(result.status_code, 400)
        result = self.call(views.SurveyDetailView, "patch", {"expected_version": survey.version, "allow_attachments": True}, survey_id=survey.id)
        self.assertEqual(result.status_code, 400)
        result = self.call(views.SurveyDetailView, "patch", {"expected_version": 1, "title": "stale"}, survey_id=survey.id)
        self.assertEqual(result.status_code, 409)

    def test_retry_version_capacity_and_reopen_journey(self):
        survey = self.publish(self.draft(response_limit=1))
        first = self.submit(survey)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.submit(survey).data["id"], first.data["id"])
        self.assertEqual(self.submit(survey, self.second).status_code, 409)
        stale = self.submit(survey, answers={"time": "일요일"})
        self.assertEqual(stale.status_code, 409)
        update = self.submit(survey, answers={"time": "일요일"}, expected_version=1)
        self.assertEqual(update.status_code, 200, update.data)
        self.assertEqual(update.data["version"], 2)
        closed = self.call(views.SurveyCloseView, "post", {"expected_version": survey.version}, survey_id=survey.id)
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(self.submit(survey, answers={"time": "일요일"}, expected_version=1).status_code, 200)
        self.assertEqual(self.submit(survey, expected_version=2).status_code, 409)
        survey.refresh_from_db()
        self.publish(survey)
        self.assertEqual(self.submit(survey, expected_version=2).status_code, 200)
        self.assertEqual(SurveyResponse.objects.filter(survey=survey).count(), 1)

    def test_permissions_tenant_recipient_and_selected_child(self):
        survey = self.publish(self.draft(audience={"mode": "selected", "student_ids": [self.student.id]}))
        detail = self.call(views.SurveyDetailView, survey_id=survey.id)
        self.assertEqual(detail.data["selected_students"], [{"id": self.student.id, "name": self.student.name}])
        self.assertEqual(self.call(views.SurveyListView, user=self.student.user).status_code, 403)
        self.assertEqual(self.call(views.SurveyDetailView, user=self.user("foreign-teacher", "teacher", self.other_tenant), tenant=self.other_tenant, survey_id=survey.id).status_code, 404)
        self.assertEqual(self.submit(survey, self.foreign).status_code, 404)
        self.assertEqual(self.submit(survey, self.second).status_code, 404)
        self.assertEqual(self.call(views.StudentSurveyListView, user=self.parent_user).status_code, 403)
        self.assertEqual(self.call(views.StudentSurveyListView, user=self.parent_user, child=self.second.id).status_code, 403)
        parent_submit = self.call(views.StudentSurveyResponseView, "put", {"answers": {"time": "토요일"}, "expected_version": 0}, user=self.parent_user, child=self.student.id, survey_id=survey.id)
        self.assertEqual(parent_submit.status_code, 200, parent_submit.data)
        self.assertEqual(SurveyResponse.objects.get(survey=survey).submitted_by_id, self.parent_user.id)

    def test_score_evidence_metadata_and_review_state_are_preserved(self):
        survey = self.publish(self.draft(kind="school_score", questions=[], score_defaults={"source": "school_exam", "academic_year": 2026, "subject": "수학"}))
        evidence = self.file()
        score = StudentReportedScore.objects.create(tenant=self.tenant, student=self.student, evidence_file=evidence, submitted_by=self.student.user, source="school_exam", academic_year=2026, semester=2, exam_round="second", subject="수학", score=95, max_score=100)
        invalid = self.submit(survey, answers={}, score_submission_ids=[])
        self.assertEqual(invalid.status_code, 400)
        valid = self.submit(survey, answers={}, score_submission_ids=[score.id])
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertEqual(valid.data["attachments"][0]["id"], evidence.id)
        score.refresh_from_db()
        self.assertEqual(score.status, "pending")
        score.status = "verified"
        score.save(update_fields=["status"])
        history = self.call(views.StudentSurveyHistoryView, student_id=self.student.id)
        self.assertEqual(history.data["results"][0]["score_submissions"][0]["status"], "verified")
        score.academic_year = 2025
        score.save(update_fields=["academic_year"])
        self.assertEqual(self.submit(survey, answers={}, score_submission_ids=[score.id], expected_version=1).status_code, 400)

    def test_attachments_cannot_cross_students_or_disappear_after_submission(self):
        survey = self.publish(self.draft(allow_attachments=True))
        foreign_file = self.file(self.second)
        self.assertEqual(self.submit(survey, attachment_ids=[foreign_file.id]).status_code, 400)
        owned_file = self.file()
        self.assertEqual(self.submit(survey, attachment_ids=[owned_file.id]).status_code, 200)
        path = f"/api/v1/storage/inventory/files/{owned_file.id}/"
        token = AccessToken.for_user(self.teacher)
        token["tenant_id"] = self.tenant.id
        token["token_version"] = self.teacher.token_version
        request = self.factory.delete(f"{path}?scope=student&student_ps={self.student.ps_number}", HTTP_AUTHORIZATION=f"Bearer {token}")
        request.tenant = self.tenant
        request.user = self.teacher
        endpoint = resolve(path)
        result = endpoint.func(request, **endpoint.kwargs)
        self.assertEqual(result.status_code, 409)
        self.assertIn(b"survey_attachment_protected", result.content)
        self.assertTrue(InventoryFile.objects.filter(id=owned_file.id).exists())
        self.assertEqual(self.submit(survey, expected_version=1, attachment_ids=[]).status_code, 200)
        self.assertFalse(SurveyResponse.objects.get(survey=survey).attachments.exists())

    def test_statistics_csv_and_unanswered_follow_saved_responses(self):
        survey = self.publish(self.draft())
        self.student.name = "=HYPERLINK(1)"
        self.student.save(update_fields=["name"])
        self.assertEqual(self.submit(survey).status_code, 200)
        stats = self.call(views.SurveyStatisticsView, survey_id=survey.id)
        self.assertEqual(stats.data["questions"][0]["options"][0]["count"], 1)
        pending = self.call(views.SurveyResponsesView, data={"response_status": "unanswered"}, survey_id=survey.id)
        self.assertEqual([row["student_id"] for row in pending.data["results"]], [self.second.id])
        export = self.call(views.SurveyExportView, survey_id=survey.id)
        content = b"".join(export.streaming_content).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK(1)", content)
        self.assertIn("미응답", content)
        self.assertIn("토요일", content)

    def test_schedule_required_answers_and_incompatible_score_defaults(self):
        future = timezone.now() + timedelta(days=1)
        survey = self.publish(self.draft(opens_at=future.isoformat(), closes_at=(future + timedelta(days=1)).isoformat()))
        self.assertEqual(self.submit(survey).status_code, 409)
        survey.opens_at = timezone.now() - timedelta(days=1)
        survey.save(update_fields=["opens_at"])
        self.assertEqual(self.submit(survey, answers={}).status_code, 400)
        self.assertEqual(self.submit(survey, answers={"time": "없는 선택지"}).status_code, 400)
        result = self.call(views.SurveyListView, "post", {"title": "잘못된 설정", "kind": "school_score", "score_defaults": {"source": "national_mock", "semester": 1}})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.call(views.SurveyCloseView, "post", {"expected_version": True}, survey_id=survey.id).status_code, 400)

    def test_permanent_student_cleanup_removes_survey_links_without_other_responses(self):
        survey = self.publish(self.draft())
        self.assertEqual(self.submit(survey).status_code, 200)
        self.assertEqual(self.submit(survey, self.second).status_code, 200)
        soft_delete_student(self.student, tenant=self.tenant)
        permanently_delete_students(tenant=self.tenant, student_ids=[self.student.id])
        self.assertFalse(SurveyRecipient.objects.filter(student_id=self.student.id).exists())
        self.assertFalse(SurveyResponse.objects.filter(student_id=self.student.id).exists())
        self.assertTrue(SurveyResponse.objects.filter(student=self.second).exists())
        self.assertTrue(Survey.objects.filter(id=survey.id).exists())
