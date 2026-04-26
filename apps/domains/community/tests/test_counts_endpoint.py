"""GET /community/posts/counts/ — 트리 카운트 집계 + 테넌트 격리."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.community.models import PostEntity, PostMapping, ScopeNode
from apps.domains.community.api.views.post_views import PostViewSet

User = get_user_model()


class TestCountsEndpoint(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="A", code="testa", is_active=True)
        self.tenant_b = Tenant.objects.create(name="B", code="testb", is_active=True)

        self.staff_a = User.objects.create_user(
            username="t_a_adm", password="pw1234", tenant=self.tenant_a, name="StaffA",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.staff_a, role="owner")

        # Tenant A: 강의2 + 차시2 (title이 unique 키이므로 명시)
        self.lec_a1 = Lecture.objects.create(tenant=self.tenant_a, name="강의A1", title="강의A1")
        self.lec_a2 = Lecture.objects.create(tenant=self.tenant_a, name="강의A2", title="강의A2")
        self.ses_a1 = Session.objects.create(lecture=self.lec_a1, order=1)
        self.ses_a2 = Session.objects.create(lecture=self.lec_a1, order=2)
        # ScopeNode는 signal로 자동 생성됨
        self.node_lec_a1 = ScopeNode.objects.get(tenant=self.tenant_a, lecture=self.lec_a1, session=None)
        self.node_lec_a2 = ScopeNode.objects.get(tenant=self.tenant_a, lecture=self.lec_a2, session=None)
        self.node_ses_a1 = ScopeNode.objects.get(tenant=self.tenant_a, lecture=self.lec_a1, session=self.ses_a1)
        self.node_ses_a2 = ScopeNode.objects.get(tenant=self.tenant_a, lecture=self.lec_a1, session=self.ses_a2)

        # Tenant B 별도 강의
        self.lec_b1 = Lecture.objects.create(tenant=self.tenant_b, name="강의B1", title="강의B1")
        self.node_lec_b1 = ScopeNode.objects.get(tenant=self.tenant_b, lecture=self.lec_b1, session=None)

        # Tenant A 공지: 1건은 GLOBAL, 1건은 강의A1 매핑, 1건은 차시A1+차시A2 매핑
        self.notice_global = PostEntity.objects.create(
            tenant=self.tenant_a, post_type="notice", title="전체 공지", content="c",
            author_role="staff", author_display_name="Admin", status="published",
        )
        self.notice_lec_a1 = PostEntity.objects.create(
            tenant=self.tenant_a, post_type="notice", title="강의A1 공지", content="c",
            author_role="staff", author_display_name="Admin", status="published",
        )
        PostMapping.objects.create(post=self.notice_lec_a1, node=self.node_lec_a1)

        self.notice_ses = PostEntity.objects.create(
            tenant=self.tenant_a, post_type="notice", title="차시 공지", content="c",
            author_role="staff", author_display_name="Admin", status="published",
        )
        PostMapping.objects.create(post=self.notice_ses, node=self.node_ses_a1)
        PostMapping.objects.create(post=self.notice_ses, node=self.node_ses_a2)

        # Tenant B 공지 1건 (격리 검증용)
        self.notice_b = PostEntity.objects.create(
            tenant=self.tenant_b, post_type="notice", title="B 공지", content="c",
            author_role="staff", status="published",
        )
        PostMapping.objects.create(post=self.notice_b, node=self.node_lec_b1)

    def _call(self, tenant, user, post_type):
        request = self.factory.get(f"/api/v1/community/posts/counts/?post_type={post_type}")
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = PostViewSet.as_view({"get": "counts"})
        return view(request)

    def test_counts_basic(self):
        """기본 집계 — total / global / by_node / by_lecture."""
        resp = self._call(self.tenant_a, self.staff_a, "notice")
        self.assertEqual(resp.status_code, 200)
        d = resp.data
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["global_count"], 1)  # mapping 없는 글 1건
        # 강의A1 노드 1건
        self.assertEqual(d["by_node_id"].get(self.node_lec_a1.id), 1)
        # 차시A1, 차시A2 각 1건
        self.assertEqual(d["by_node_id"].get(self.node_ses_a1.id), 1)
        self.assertEqual(d["by_node_id"].get(self.node_ses_a2.id), 1)
        # 강의 단위 distinct 카운트 — 강의A1에 매핑된 글 = 강의공지 + 차시공지(distinct=2)
        self.assertEqual(d["by_lecture_id"].get(self.lec_a1.id), 2)

    def test_counts_tenant_isolation(self):
        """다른 테넌트 글은 카운트에서 제외."""
        resp = self._call(self.tenant_a, self.staff_a, "notice")
        self.assertEqual(resp.status_code, 200)
        # Tenant B의 강의B1 노드 ID는 결과에 없어야 함
        self.assertNotIn(self.node_lec_b1.id, resp.data["by_node_id"])
        self.assertNotIn(self.lec_b1.id, resp.data["by_lecture_id"])

    def test_counts_invalid_post_type(self):
        """허용되지 않는 post_type → 400."""
        resp = self._call(self.tenant_a, self.staff_a, "invalid_type")
        self.assertEqual(resp.status_code, 400)

    def test_counts_missing_tenant(self):
        """tenant 미해석 → 403."""
        request = self.factory.get("/api/v1/community/posts/counts/?post_type=notice")
        force_authenticate(request, user=self.staff_a)
        # request.tenant 미설정
        view = PostViewSet.as_view({"get": "counts"})
        resp = view(request)
        self.assertEqual(resp.status_code, 403)

    def test_counts_excludes_unpublished(self):
        """draft/archived 글은 카운트에서 제외."""
        PostEntity.objects.create(
            tenant=self.tenant_a, post_type="notice", title="draft", content="c",
            author_role="staff", status="draft",
        )
        resp = self._call(self.tenant_a, self.staff_a, "notice")
        self.assertEqual(resp.data["total"], 3)  # draft는 미포함
