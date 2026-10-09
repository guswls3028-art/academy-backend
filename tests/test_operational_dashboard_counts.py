"""Operational totals must not depend on the first page of detailed records."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.exams.models import Exam
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class OperationalDashboardCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-dashboard-counts", name="Dashboard QA")
        cls.other = Tenant.objects.create(code="qa-dashboard-other", name="Other QA")
        cls.teacher = get_user_model().objects.create_user(username="qa-dashboard-teacher", tenant=cls.tenant, must_change_password=False)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.teacher, role="teacher")
        cls.student_user = get_user_model().objects.create_user(username="qa-dashboard-student", tenant=cls.tenant, must_change_password=False)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.student_user, role="student")
        cls.student = Student.objects.create(tenant=cls.tenant, user=cls.student_user, ps_number="COUNT01", omr_code="10000001", name="QA Student")

    def client_for(self, user=None, tenant=None):
        user, tenant = user or self.teacher, tenant or self.tenant
        token = AccessToken.for_user(user)
        token["tenant_id"] = tenant.id
        token["token_version"] = user.token_version or 0
        client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=tenant.code)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def post(self, **fields):
        return PostEntity.objects.create(tenant=self.tenant, post_type="qna", title="QA question", created_by=self.student, author_role="student", **fields)

    def counts(self, client=None):
        response = (client or self.client_for()).get("/api/v1/results/admin/teacher-dashboard-counts/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_counts_include_unanswered_questions_beyond_one_hundred_recent_answers(self):
        pending = [self.post() for _ in range(105)]
        PostEntity.objects.filter(id__in=[p.id for p in pending]).update(created_at=timezone.now() - timedelta(days=1))
        for _ in range(100):
            PostReply.objects.create(tenant=self.tenant, post=self.post(), content="Answered", author_role="staff")
        client = self.client_for()
        page = client.get("/api/v1/community/admin/posts/?post_type=qna&page_size=100").json()
        self.assertEqual(page["count"], 205)
        self.assertEqual(sum(p["replies_count"] == 0 for p in page["results"]), 0)
        self.assertEqual(self.counts(client).get("qna_pending"), 105)
        PostReply.objects.create(tenant=self.tenant, post=pending[0], content="New answer", author_role="staff")
        self.assertEqual(self.counts(client).get("qna_pending"), 104)

    def test_processing_total_is_not_capped_at_two_hundred_and_excludes_homework(self):
        Submission.objects.bulk_create([
            Submission(tenant=self.tenant, user=self.student_user, target_type="exam", target_id=index + 1,
                       source="omr_scan", status="submitted") for index in range(205)
        ])
        Submission.objects.create(tenant=self.tenant, user=self.student_user, target_type="homework", target_id=1, source="homework_image", status="submitted")
        Submission.objects.create(tenant=self.tenant, user=self.student_user, target_type="exam", target_id=999, source="omr_scan", status="done")
        legacy = self.client_for().get("/api/v1/submissions/submissions/pending/?filter=pending")
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertEqual(len(legacy.json()), 200)
        self.assertEqual(self.counts().get("submission_pending"), 205)

    def test_unpublished_deleted_author_internal_memo_and_other_tenant_do_not_count(self):
        self.post()
        self.post(status="archived")
        self.post(status="draft")
        for fields in ({"author_role": "student"}, {"author_role": "staff"}, {"author_role": "student", "category_label": "teacher_internal_memo"}):
            PostEntity.objects.create(tenant=self.tenant, post_type="counsel", title="QA counsel", created_by=self.student, **fields)
        PostEntity.objects.create(tenant=self.other, post_type="qna", title="Foreign", author_role="student")
        self.assertEqual(self.counts().get("qna_pending"), 1)
        self.assertEqual(self.counts().get("counsel_pending"), 1)
        Student.objects.filter(pk=self.student.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.counts().get("qna_pending"), 0)
        self.assertEqual(self.counts().get("counsel_pending"), 0)

    def test_student_and_foreign_tenant_do_not_gain_staff_counts(self):
        self.assertEqual(self.client_for(self.student_user).get("/api/v1/results/admin/teacher-dashboard-counts/").status_code, 403)
        self.assertEqual(self.client_for(tenant=self.other).get("/api/v1/results/admin/teacher-dashboard-counts/").status_code, 401)

    def test_empty_academy_returns_explicit_zero_for_each_count(self):
        self.assertEqual(self.counts(), {"video_failed": 0, "qna_pending": 0, "counsel_pending": 0, "submission_pending": 0})

    def test_active_exam_total_excludes_templates_inactive_and_other_tenant(self):
        Exam.objects.bulk_create([
            Exam(tenant=self.tenant, title=f"QA exam {i}", exam_type="regular", is_active=True)
            for i in range(205)
        ])
        Exam.objects.create(tenant=self.tenant, title="QA template", exam_type="template", is_active=True)
        Exam.objects.create(tenant=self.tenant, title="QA inactive", exam_type="regular", is_active=False)
        Exam.objects.create(tenant=self.other, title="Foreign exam", exam_type="regular", is_active=True)
        response = self.client_for().get("/api/v1/exams/?exam_type=regular&page_size=1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 205)
        self.assertEqual(len(response.json()["results"]), 1)
