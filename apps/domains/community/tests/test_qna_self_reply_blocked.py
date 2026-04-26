"""B-2: 학생은 QnA/Counsel에 답변(reply) 불가 (본인 글 포함)."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.students.models import Student
from apps.domains.community.models import PostEntity
from apps.domains.community.api.views.post_views import PostViewSet

User = get_user_model()


class TestQnaSelfReplyBlocked(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="T", code="t1", is_active=True)
        self.user = User.objects.create_user(
            username="t_stu", password="pw1234",
            tenant=self.tenant, name="홍길동",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.user,
            ps_number="S001", name="홍길동",
            phone="01011112222", parent_phone="01033334444", omr_code="11112222",
        )
        # 본인 QnA
        self.qna = PostEntity.objects.create(
            tenant=self.tenant, post_type="qna",
            title="Q", content="c",
            created_by=self.student, author_role="student",
            author_display_name="홍길동", status="published",
        )
        # 본인 counsel
        self.counsel = PostEntity.objects.create(
            tenant=self.tenant, post_type="counsel",
            title="상담", content="c",
            created_by=self.student, author_role="student",
            author_display_name="홍길동", status="published",
        )
        # 공개 board
        self.board = PostEntity.objects.create(
            tenant=self.tenant, post_type="board",
            title="공지", content="c",
            created_by=None, author_role="staff",
            author_display_name="관리자", status="published",
        )
        # 자료실 (일방향 정책)
        self.materials = PostEntity.objects.create(
            tenant=self.tenant, post_type="materials",
            title="자료", content="c",
            created_by=None, author_role="staff",
            author_display_name="관리자", status="published",
        )

    def _post_reply(self, post):
        request = self.factory.post(
            f"/api/v1/community/posts/{post.id}/replies/",
            data={"content": "내 답변"}, format="json",
        )
        request.tenant = self.tenant
        # request.user.student_profile 가 student를 반환하도록 — 실제 핸들러는 student membership에서 resolve
        force_authenticate(request, user=self.user)
        view = PostViewSet.as_view({"post": "replies"})
        return view(request, pk=post.id)

    def test_student_cannot_reply_to_own_qna(self):
        """본인 QnA에도 답변 차단."""
        resp = self._post_reply(self.qna)
        self.assertEqual(resp.status_code, 403)

    def test_student_cannot_reply_to_own_counsel(self):
        resp = self._post_reply(self.counsel)
        self.assertEqual(resp.status_code, 403)

    def test_student_can_comment_on_board(self):
        """공개 게시판은 댓글 가능."""
        resp = self._post_reply(self.board)
        self.assertEqual(resp.status_code, 201, f"resp: {resp.data}")

    def test_student_cannot_comment_on_materials(self):
        """자료실은 일방향 — 학생 댓글 차단."""
        resp = self._post_reply(self.materials)
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_comment_on_materials(self):
        """자료실은 일방향 — staff도 댓글 차단 (정책 일관성)."""
        # staff 사용자 생성
        staff = User.objects.create_user(
            username="t_adm", password="pw1234", tenant=self.tenant, name="Admin",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=staff, role="owner")
        request = self.factory.post(
            f"/api/v1/community/posts/{self.materials.id}/replies/",
            data={"content": "운영 노트"}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=staff)
        view = PostViewSet.as_view({"post": "replies"})
        resp = view(request, pk=self.materials.id)
        self.assertEqual(resp.status_code, 403)

    def test_download_only_constant_includes_materials(self):
        """SSOT: DOWNLOAD_ONLY_POST_TYPES에 materials 포함 (정책 변경 시 테스트도 같이)."""
        from apps.domains.community.models.post import DOWNLOAD_ONLY_POST_TYPES
        self.assertIn("materials", DOWNLOAD_ONLY_POST_TYPES)
