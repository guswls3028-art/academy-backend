"""Community 도메인 테넌트 절대 격리 검증.

§B 절대 격리 원칙: 어떤 진입점에서도 다른 테넌트 글에 접근 불가.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.students.models import Student
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.community.api.views.post_views import PostViewSet

User = get_user_model()


class TestPostTenantIsolation(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="A", code="testa", is_active=True)
        self.tenant_b = Tenant.objects.create(name="B", code="testb", is_active=True)

        self.user_a = User.objects.create_user(
            username="ta_adm", password="pw1234", tenant=self.tenant_a, name="A",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.user_a, role="owner")

        # B 테넌트의 학생 + QnA
        self.user_b_stu = User.objects.create_user(
            username="tb_stu", password="pw1234", tenant=self.tenant_b, name="B학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.user_b_stu, role="student")
        self.student_b = Student.objects.create(
            tenant=self.tenant_b, user=self.user_b_stu,
            ps_number="B001", name="B학생",
            phone="01055556666", parent_phone="01077778888", omr_code="55556666",
        )
        self.post_b_qna = PostEntity.objects.create(
            tenant=self.tenant_b, post_type="qna", title="B의 질문", content="c",
            created_by=self.student_b, author_role="student",
            author_display_name="B학생", status="published",
        )
        self.post_b_notice = PostEntity.objects.create(
            tenant=self.tenant_b, post_type="notice", title="B의 공지", content="c",
            author_role="staff", author_display_name="B관리자", status="published",
        )

    def test_retrieve_cross_tenant_returns_404(self):
        """A staff가 B의 post_id로 retrieve → 404."""
        request = self.factory.get(f"/api/v1/community/posts/{self.post_b_qna.id}/")
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a
        view = PostViewSet.as_view({"get": "retrieve"})
        resp = view(request, pk=self.post_b_qna.id)
        self.assertEqual(resp.status_code, 404)

    def test_list_excludes_other_tenants(self):
        """A로 list 호출 시 B의 글이 절대 안 섞임."""
        # A에도 같은 post_type 글 1건
        PostEntity.objects.create(
            tenant=self.tenant_a, post_type="notice", title="A 공지", content="c",
            author_role="staff", status="published",
        )
        request = self.factory.get("/api/v1/community/posts/notices/")
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a
        view = PostViewSet.as_view({"get": "notices"})
        resp = view(request)
        self.assertEqual(resp.status_code, 200)
        titles = [r["title"] for r in resp.data["results"]]
        self.assertIn("A 공지", titles)
        self.assertNotIn("B의 공지", titles)

    def test_admin_list_tenant_scoped(self):
        """admin 목록도 테넌트 스코프."""
        from apps.domains.community.api.views.admin_views import AdminPostViewSet
        request = self.factory.get("/api/v1/community/admin/posts/?post_type=qna")
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a
        view = AdminPostViewSet.as_view({"get": "list"})
        resp = view(request)
        self.assertEqual(resp.status_code, 200)
        # B의 QnA가 응답에 없음
        ids = [r["id"] for r in resp.data["results"]]
        self.assertNotIn(self.post_b_qna.id, ids)

    def test_update_cross_tenant_blocked(self):
        """A staff가 B의 post를 PATCH → 404 (get_object가 tenant scope에서 못 찾음)."""
        request = self.factory.patch(
            f"/api/v1/community/posts/{self.post_b_qna.id}/",
            data={"title": "hijacked"}, format="json",
        )
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a
        view = PostViewSet.as_view({"patch": "partial_update"})
        resp = view(request, pk=self.post_b_qna.id)
        # 다른 테넌트 post는 queryset에서 안 보임 → 404
        self.assertIn(resp.status_code, (403, 404))
        # B의 post는 그대로
        self.post_b_qna.refresh_from_db()
        self.assertEqual(self.post_b_qna.title, "B의 질문")

    def test_reply_cross_tenant_blocked(self):
        """A staff가 B의 post에 답변 등록 시도 → 404."""
        request = self.factory.post(
            f"/api/v1/community/posts/{self.post_b_qna.id}/replies/",
            data={"content": "외부 답변"}, format="json",
        )
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a
        view = PostViewSet.as_view({"post": "replies"})
        resp = view(request, pk=self.post_b_qna.id)
        self.assertEqual(resp.status_code, 404)
        # B의 post에 reply 안 만들어짐
        self.assertEqual(PostReply.objects.filter(post=self.post_b_qna).count(), 0)


class TestStudentPostScope(TestCase):
    """학생 권한 — 본인 글 / 공개 타입만 접근."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="T", code="t1", is_active=True)
        # 학생 A
        self.user_a = User.objects.create_user(
            username="stu_a", password="pw1234", tenant=self.tenant, name="A",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user_a, role="student")
        self.student_a = Student.objects.create(
            tenant=self.tenant, user=self.user_a,
            ps_number="A001", name="A",
            phone="01011112222", parent_phone="01033334444", omr_code="11112222",
        )
        # 학생 B
        self.user_b = User.objects.create_user(
            username="stu_b", password="pw1234", tenant=self.tenant, name="B",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user_b, role="student")
        self.student_b = Student.objects.create(
            tenant=self.tenant, user=self.user_b,
            ps_number="B001", name="B",
            phone="01055556666", parent_phone="01077778888", omr_code="55556666",
        )
        # B의 QnA
        self.qna_b = PostEntity.objects.create(
            tenant=self.tenant, post_type="qna", title="B 질문", content="c",
            created_by=self.student_b, author_role="student",
            author_display_name="B", status="published",
        )

    def test_student_cannot_read_other_student_qna(self):
        """B-1+B2: 학생 A는 학생 B의 QnA retrieve 불가."""
        request = self.factory.get(f"/api/v1/community/posts/{self.qna_b.id}/")
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant
        view = PostViewSet.as_view({"get": "retrieve"})
        resp = view(request, pk=self.qna_b.id)
        self.assertEqual(resp.status_code, 404)

    def test_student_can_read_own_qna(self):
        """본인 QnA는 retrieve 가능."""
        request = self.factory.get(f"/api/v1/community/posts/{self.qna_b.id}/")
        force_authenticate(request, user=self.user_b)
        request.tenant = self.tenant
        view = PostViewSet.as_view({"get": "retrieve"})
        resp = view(request, pk=self.qna_b.id)
        self.assertEqual(resp.status_code, 200)

    def test_student_cannot_modify_other_student_qna(self):
        """학생 A는 학생 B의 QnA 수정 불가.

        get_queryset이 학생에게 본인 글만 노출하므로 다른 학생 글은 404
        (존재 노출 회피 — 403보다 보안상 우수).
        """
        request = self.factory.patch(
            f"/api/v1/community/posts/{self.qna_b.id}/",
            data={"title": "hijack"}, format="json",
        )
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant
        view = PostViewSet.as_view({"patch": "partial_update"})
        resp = view(request, pk=self.qna_b.id)
        self.assertIn(resp.status_code, (403, 404))
        self.qna_b.refresh_from_db()
        self.assertEqual(self.qna_b.title, "B 질문")
