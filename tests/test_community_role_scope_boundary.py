"""Exercise community visibility and scope changes through real tenant JWT requests."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.models import (
    PostEntity, PostLike, PostMapping, PostReply, PostReplyLike, ScopeNode,
)
from apps.domains.lectures.models import Lecture
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class CommunityRoleScopeBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-community-roles", name="Community QA")
        cls.other = Tenant.objects.create(code="qa-community-other", name="Other QA")
        cls.users = {}
        for role in ("owner", "admin", "staff", "teacher", "student", "parent"):
            user = get_user_model().objects.create_user(
                username=f"qa-community-{role}", tenant=cls.tenant,
                is_staff=True, must_change_password=False,
            )
            TenantMembership.ensure_active(tenant=cls.tenant, user=user, role=role)
            cls.users[role] = user
        cls.parent = Parent.objects.create(
            tenant=cls.tenant, user=cls.users["parent"], name="QA Parent", phone="01000001111",
        )
        cls.student = Student.objects.create(
            tenant=cls.tenant, user=cls.users["student"], parent=cls.parent,
            ps_number="COMM01", omr_code="10000001", name="QA Student",
        )
        cls.other_student = Student.objects.create(
            tenant=cls.tenant, ps_number="COMM02", omr_code="10000002", name="Other Student",
            user=get_user_model().objects.create_user(username="qa-community-other-student", tenant=cls.tenant),
        )
        cls.private_post = PostEntity.objects.create(
            tenant=cls.tenant, post_type="qna", title="Private question",
            content="Private content", created_by=cls.other_student, author_role="student",
        )
        cls.own_post = PostEntity.objects.create(
            tenant=cls.tenant, post_type="qna", title="Own question", content="Own content",
            created_by=cls.student, author_role="student",
        )
        cls.reply = PostReply.objects.create(
            tenant=cls.tenant, post=cls.private_post, content="Private reply", author_role="staff",
        )
        cls.nodes = []
        for tenant, title in ((cls.tenant, "First"), (cls.tenant, "Second"), (cls.other, "Foreign")):
            lecture = Lecture.objects.create(tenant=tenant, title=title, name=title)
            cls.nodes.append(ScopeNode.objects.create(
                tenant=tenant, lecture=lecture, level=ScopeNode.Level.COURSE,
            ))
        cls.scoped_post = PostEntity.objects.create(
            tenant=cls.tenant, post_type="board", title="Restricted class", content="Class content",
        )
        PostMapping.objects.create(post=cls.scoped_post, node=cls.nodes[0])
        cls.foreign_post = PostEntity.objects.create(
            tenant=cls.other, post_type="board", title="Foreign content",
        )

    def client_for(self, role):
        user = self.users[role]
        token = AccessToken.for_user(user)
        token["tenant_id"] = self.tenant.id
        token["token_version"] = user.token_version or 0
        headers = {"HTTP_X_STUDENT_ID": str(self.student.id)} if role == "parent" else {}
        client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code, **headers)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_limited_roles_cannot_read_private_posts_or_replies_with_global_flags(self):
        for role in ("student", "parent"):
            for field in ("is_staff", "is_superuser"):
                self.users[role].is_staff = field == "is_staff"
                self.users[role].is_superuser = field == "is_superuser"
                self.users[role].save(update_fields=["is_staff", "is_superuser"])
                for post_type, post_status in (("qna", "published"), ("counsel", "published"), ("board", "draft")):
                    self.private_post.post_type = post_type
                    self.private_post.status = post_status
                    self.private_post.save(update_fields=["post_type", "status"])
                    for suffix in ("", "replies/"):
                        with self.subTest(role=role, flag=field, post_type=post_type, suffix=suffix):
                            response = self.client_for(role).get(f"/api/v1/community/posts/{self.private_post.id}/{suffix}")
                            self.assertEqual(response.status_code, 404, response.content)

    def test_private_reactions_do_not_bypass_visibility(self):
        client = self.client_for("student")
        for suffix in ("like/", f"replies/{self.reply.id}/like/"):
            with self.subTest(suffix=suffix):
                response = client.post(f"/api/v1/community/posts/{self.private_post.id}/{suffix}", {}, format="json")
                self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(PostLike.objects.exists())
        self.assertFalse(PostReplyLike.objects.exists())

    def test_limited_reader_cannot_read_an_unenrolled_class_post(self):
        for role in ("student", "parent"):
            with self.subTest(role=role):
                response = self.client_for(role).get(f"/api/v1/community/posts/{self.scoped_post.id}/")
                self.assertEqual(response.status_code, 404, response.content)

    def test_current_role_controls_scope_changes_not_global_flags(self):
        for role in ("student", "parent", "teacher"):
            for field in ("is_staff", "is_superuser"):
                with self.subTest(role=role, flag=field):
                    self.users[role].is_staff = field == "is_staff"
                    self.users[role].is_superuser = field == "is_superuser"
                    self.users[role].save(update_fields=["is_staff", "is_superuser"])
                    response = self.client_for(role).patch(
                        f"/api/v1/community/posts/{self.scoped_post.id}/nodes/",
                        {"node_ids": []}, format="json",
                    )
                    self.assertEqual(response.status_code, 403, response.content)
                    self.assertEqual(list(self.scoped_post.mappings.values_list("node_id", flat=True)), [self.nodes[0].id])

    def test_student_cannot_change_own_post_operating_fields(self):
        client = self.client_for("student")
        path = f"/api/v1/community/posts/{self.own_post.id}/"
        rejected = client.patch(path, {"is_pinned": True, "status": "archived"}, format="json")
        self.assertEqual(rejected.status_code, 403, rejected.content)
        saved = client.patch(path, {"title": "Edited question"}, format="json")
        self.assertEqual(saved.status_code, 200, saved.content)
        readback = client.get(path)
        self.assertEqual(readback.json()["title"], "Edited question")
        self.assertEqual(readback.json()["status"], "published")
        self.assertFalse(readback.json()["is_pinned"])

    def test_legitimate_staff_read_private_posts_without_global_flags(self):
        for role in ("owner", "admin", "staff", "teacher"):
            with self.subTest(role=role):
                self.users[role].is_staff = False
                self.users[role].save(update_fields=["is_staff"])
                client = self.client_for(role)
                self.assertEqual(client.get(f"/api/v1/community/posts/{self.private_post.id}/").status_code, 200)
                self.assertContains(client.get(f"/api/v1/community/posts/{self.private_post.id}/replies/"), "Private reply")

    def test_scope_managers_can_save_reload_and_explicitly_remove_mappings(self):
        path = f"/api/v1/community/posts/{self.scoped_post.id}/nodes/"
        for role in ("owner", "admin", "staff"):
            with self.subTest(role=role):
                self.users[role].is_staff = False
                self.users[role].save(update_fields=["is_staff"])
                client = self.client_for(role)
                for node_ids, expected in (([str(self.nodes[1].id), self.nodes[1].id], [self.nodes[1].id]), ([], [])):
                    response = client.patch(path, {"node_ids": node_ids}, format="json")
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(list(self.scoped_post.mappings.values_list("node_id", flat=True)), expected)
                    self.assertEqual(client.get(f"/api/v1/community/posts/{self.scoped_post.id}/").status_code, 200)

    def test_selected_child_questions_remain_editable_but_parent_reactions_are_read_only(self):
        client = self.client_for("parent")
        path = f"/api/v1/community/posts/{self.own_post.id}/"
        self.assertEqual(client.get(path, HTTP_X_STUDENT_ID=str(self.student.id)).status_code, 200)
        self.assertEqual(client.post(path + "like/", {}, format="json").status_code, 403)
        saved = client.patch(path, {"title": "Parent edited question"}, format="json", HTTP_X_STUDENT_ID=str(self.student.id))
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(client.get(path, HTTP_X_STUDENT_ID=str(self.student.id)).json()["title"], "Parent edited question")

    def test_owner_can_create_global_and_scoped_posts_and_reload(self):
        client = self.client_for("owner")
        for scope in ({}, {"node_ids": []}, {"node_ids": [str(self.nodes[0].id)]}):
            with self.subTest(scope=scope):
                response = client.post("/api/v1/community/posts/", {"title": "New board", "content": "New", "post_type": "board", **scope}, format="json")
                self.assertEqual(response.status_code, 201, response.content)
                post = PostEntity.objects.get(id=response.json()["id"])
                self.assertEqual(list(post.mappings.values_list("node_id", flat=True)), [int(n) for n in scope.get("node_ids", [])])
                self.assertEqual(client.get(f"/api/v1/community/posts/{post.id}/").status_code, 200)

    def test_foreign_post_and_node_are_rejected_without_changing_mappings(self):
        client = self.client_for("owner")
        foreign = client.patch(f"/api/v1/community/posts/{self.foreign_post.id}/nodes/", {"node_ids": []}, format="json")
        self.assertEqual(foreign.status_code, 404, foreign.content)
        invalid = client.patch(f"/api/v1/community/posts/{self.scoped_post.id}/nodes/", {"node_ids": [self.nodes[2].id]}, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertEqual(list(self.scoped_post.mappings.values_list("node_id", flat=True)), [self.nodes[0].id])

    def test_invalid_or_missing_scope_cannot_silently_make_post_global(self):
        client = self.client_for("owner")
        for data in ([], {}, {"node_ids": None}, {"node_ids": False}, {"node_ids": ""}, {"node_ids": {}}, {"node_ids": [True]}, {"node_ids": [self.nodes[1].id + 0.9]}, {"node_ids": [2**63]}, {"node_ids": [0]}, {"node_ids": [-1]}, {"node_ids": [[1]]}):
            with self.subTest(data=data):
                response = client.patch(f"/api/v1/community/posts/{self.scoped_post.id}/nodes/", data, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(list(self.scoped_post.mappings.values_list("node_id", flat=True)), [self.nodes[0].id])

    def test_create_rejects_ambiguous_scope_without_creating_post(self):
        client = self.client_for("owner")
        initial = PostEntity.objects.count()
        for node_ids in (None, False, "", {}, [True], [self.nodes[1].id + 0.9]):
            with self.subTest(node_ids=node_ids):
                response = client.post("/api/v1/community/posts/", {"title": "New board", "content": "New", "post_type": "board", "node_ids": node_ids}, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(PostEntity.objects.count(), initial)

    def test_revoked_membership_rejects_previously_issued_token(self):
        client = self.client_for("owner")
        TenantMembership.objects.filter(tenant=self.tenant, user=self.users["owner"]).update(is_active=False)
        response = client.get(f"/api/v1/community/posts/{self.private_post.id}/")
        self.assertEqual(response.status_code, 401, response.content)
