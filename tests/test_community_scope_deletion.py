"""Scope deletion must not silently widen a post's audience."""
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections
from django.db.models.deletion import Collector, RestrictedError
from django.test import TransactionTestCase
from rest_framework.test import force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.models import PostEntity, PostMapping, ScopeNode
from apps.domains.community.tests.test_p0_community_hardening import CommunityHardeningFixture
from apps.domains.lectures.models import Lecture, Session
from apps.domains.lectures.views import LectureViewSet, SessionViewSet


class TestCommunityScopeDeletion(CommunityHardeningFixture):
    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.foreign_owner = get_user_model().objects.create_user(
            tenant=self.other_tenant, username="scope-delete-foreign", password="test-only",
        )
        TenantMembership.ensure_active(
            tenant=self.other_tenant, user=self.foreign_owner, role="owner",
        )

    def make_scope(self, kind):
        self.sequence += 1
        lecture = Lecture.objects.create(
            tenant=self.tenant, name=f"Restricted {self.sequence}",
            title=f"Restricted {self.sequence}",
        )
        if kind == "lecture":
            return lecture, ScopeNode.objects.get(lecture=lecture, session=None)
        session = Session.objects.create(lecture=lecture, order=1, title="Restricted session")
        return session, ScopeNode.objects.get(session=session)

    def make_post(self, node, post_type="notice", status="published"):
        post = PostEntity.objects.create(
            tenant=self.tenant, post_type=post_type, status=status, author_role="staff",
            title="Restricted content", content="Retained content",
        )
        PostMapping.objects.create(post=post, node=node)
        return post

    def delete_scope(self, target, user=None, tenant=None):
        kind = "lectures" if isinstance(target, Lecture) else "sessions"
        view = LectureViewSet if kind == "lectures" else SessionViewSet
        request = self._request("delete", user or self.owner, f"/api/v1/lectures/{kind}/{target.pk}/")
        if tenant is not None:
            request.tenant = tenant
        return view.as_view({"delete": "destroy"})(request, pk=target.pk)

    def read_post(self, post, user=None, tenant=None):
        request = self._request("get", user or self.student_user, f"/api/v1/community/posts/{post.pk}/")
        if tenant is not None:
            request.tenant = tenant
            force_authenticate(request, user=user)
        return PostViewSet.as_view({"get": "retrieve"})(request, pk=post.pk)

    def change_targets(self, post, node_ids):
        request = self._request("patch", self.owner, f"/api/v1/community/posts/{post.pk}/nodes/", {"node_ids": node_ids})
        return PostViewSet.as_view({"patch": "update_nodes"})(request, pk=post.pk)

    def assert_preserved(self, target, post, mapping_ids):
        self.assertTrue(type(target).objects.filter(pk=target.pk).exists())
        post.refresh_from_db()
        self.assertEqual(post.content, "Retained content")
        self.assertEqual(set(post.mappings.values_list("node_id", flat=True)), set(mapping_ids))
        self.assertEqual(self.read_post(post).status_code, 404)
        self.assertEqual(self.read_post(post, self.foreign_owner, self.other_tenant).status_code, 404)

    def assert_api_preserves_scope(self, kind):
        for post_type in ("notice", "material"):
            for status in ("draft", "published"):
                with self.subTest(kind=kind, post_type=post_type, status=status):
                    target, node = self.make_scope(kind)
                    post = self.make_post(node, post_type, status)
                    self.assertEqual(self.read_post(post).status_code, 404)
                    response = self.delete_scope(target)
                    self.assertEqual(response.status_code, 403, response.data)
                    self.assertIn("커뮤니티", str(response.data))
                    self.assertIn("다시 시도", str(response.data))
                    self.assert_preserved(target, post, [node.pk])
                    self.assertEqual(post.status, status)

    def test_lecture_api_preserves_restricted_posts(self):
        self.assert_api_preserves_scope("lecture")

    def test_session_api_preserves_restricted_posts(self):
        self.assert_api_preserves_scope("session")

    def test_lecture_cascade_preserves_child_session_and_multiple_targets(self):
        session, node = self.make_scope("session")
        _, other_node = self.make_scope("lecture")
        post = self.make_post(node)
        PostMapping.objects.create(post=post, node=other_node)
        response = self.delete_scope(session.lecture)
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_preserved(session, post, [node.pk, other_node.pk])
        self.assertTrue(Lecture.objects.filter(pk=session.lecture_id).exists())

    def test_model_and_queryset_deletes_cannot_bypass_scope_protection(self):
        for kind in ("lecture", "session", "node"):
            for bulk in (False, True):
                with self.subTest(kind=kind, bulk=bulk):
                    target, node = self.make_scope("session" if kind == "session" else "lecture")
                    post = self.make_post(node)
                    delete_target = node if kind == "node" else target
                    if bulk:
                        delete_target = type(delete_target).objects.filter(pk=delete_target.pk)
                    with self.assertRaises(RestrictedError):
                        delete_target.delete()
                    self.assert_preserved(target, post, [node.pk])

    def test_collector_restriction_after_preflight_returns_recovery_response(self):
        for kind in ("lecture", "session"):
            with self.subTest(kind=kind):
                target, node = self.make_scope(kind)
                post = self.make_post(node)
                # Model protection remains authoritative after a stale preflight.
                with patch(f"apps.domains.lectures.views.first_{kind}_delete_blocker", return_value=None):
                    response = self.delete_scope(target)
                self.assertEqual(response.status_code, 403, response.data)
                self.assertIn("다시 시도", str(response.data))
                self.assert_preserved(target, post, [node.pk])

    def test_empty_scope_delete_still_succeeds_for_authorized_staff(self):
        for kind in ("lecture", "session"):
            for user in (self.owner, self.teacher):
                with self.subTest(kind=kind, role=user.username):
                    target, _ = self.make_scope(kind)
                    response = self.delete_scope(target, user)
                    self.assertEqual(response.status_code, 204, response.data)
                    self.assertFalse(type(target).objects.filter(pk=target.pk).exists())

    def test_students_and_other_tenants_cannot_delete_scopes(self):
        for kind in ("lecture", "session"):
            with self.subTest(kind=kind):
                target, _ = self.make_scope(kind)
                self.assertEqual(self.delete_scope(target, self.student_user).status_code, 403)
                self.assertEqual(self.delete_scope(target, self.foreign_owner, self.other_tenant).status_code, 404)
                self.assertTrue(type(target).objects.filter(pk=target.pk).exists())

    def test_explicit_global_publication_allows_scope_delete_and_persists(self):
        for kind in ("lecture", "session"):
            with self.subTest(kind=kind):
                target, node = self.make_scope(kind)
                post = self.make_post(node)
                self.assertEqual(self.read_post(post).status_code, 404)
                response = self.change_targets(post, [])
                self.assertEqual(response.status_code, 200, response.data)
                self.assertFalse(post.mappings.exists())
                self.assertEqual(self.delete_scope(target).status_code, 204)
                post.refresh_from_db()
                self.assertEqual(post.status, "published")
                self.assertEqual(self.read_post(post).status_code, 200)
                self.assertEqual(self.read_post(post, self.foreign_owner, self.other_tenant).status_code, 404)

    def test_reassignment_preserves_content_and_allows_old_scope_delete(self):
        target, node = self.make_scope("lecture")
        _, replacement = self.make_scope("lecture")
        post = self.make_post(node)
        response = self.change_targets(post, [replacement.pk])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.delete_scope(target).status_code, 204)
        post.refresh_from_db()
        self.assertEqual(list(post.mappings.values_list("node_id", flat=True)), [replacement.pk])
        self.assertEqual(post.content, "Retained content")
        self.assertEqual(self.read_post(post).status_code, 404)

    def test_normal_post_delete_allows_scope_delete_retry(self):
        for kind in ("lecture", "session"):
            with self.subTest(kind=kind):
                target, node = self.make_scope(kind)
                post = self.make_post(node)
                self.assertEqual(self.delete_scope(target).status_code, 403)
                request = self._request("delete", self.owner, f"/api/v1/community/posts/{post.pk}/")
                response = PostViewSet.as_view({"delete": "destroy"})(request, pk=post.pk)
                self.assertIn(response.status_code, (200, 204), response.data)
                self.assertFalse(PostEntity.objects.filter(pk=post.pk).exists())
                self.assertFalse(PostMapping.objects.filter(post_id=post.pk).exists())
                self.assertEqual(self.delete_scope(target).status_code, 204)

    def test_whole_tenant_cascade_cleans_its_posts_and_preserves_other_tenant(self):
        tenant = Tenant.objects.create(name="Disposable scope QA", code="qa-scope-delete")
        lecture = Lecture.objects.create(tenant=tenant, name="Cleanup", title="Cleanup")
        session = Session.objects.create(lecture=lecture, order=1, title="Cleanup session")
        nodes = list(ScopeNode.objects.filter(tenant=tenant))
        post = PostEntity.objects.create(tenant=tenant, post_type="notice", status="published", title="Cleanup", content="Synthetic")
        PostMapping.objects.bulk_create([PostMapping(post=post, node=node) for node in nodes])
        foreign_snapshot = set(PostMapping.objects.values_list("pk", "post_id", "node_id")) - set(post.mappings.values_list("pk", "post_id", "node_id"))
        Tenant.objects.filter(pk=tenant.pk).delete()
        self.assertFalse(Tenant.objects.filter(pk=tenant.pk).exists())
        self.assertFalse(Lecture.objects.filter(pk=lecture.pk).exists())
        self.assertFalse(Session.objects.filter(pk=session.pk).exists())
        self.assertFalse(ScopeNode.objects.filter(tenant_id=tenant.pk).exists())
        self.assertFalse(PostEntity.objects.filter(tenant_id=tenant.pk).exists())
        self.assertFalse(PostMapping.objects.filter(post_id=post.pk).exists())
        self.assertSetEqual(set(PostMapping.objects.values_list("pk", "post_id", "node_id")), foreign_snapshot)


class TestScopeDeletionRacePostgres(TransactionTestCase):
    def test_mapping_committed_after_collection_prevents_scope_deletion(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL deferred FK and concurrent transactions required")
        for kind in ("lecture", "session"):
            with self.subTest(kind=kind):
                tenant = Tenant.objects.create(name=f"Race {kind}", code=f"qa-scope-race-{kind}")
                lecture = Lecture.objects.create(tenant=tenant, name="Race", title="Race")
                target = lecture if kind == "lecture" else Session.objects.create(lecture=lecture, order=1, title="Race session")
                node = ScopeNode.objects.get(lecture=lecture, session=None if kind == "lecture" else target)
                post = PostEntity.objects.create(tenant=tenant, title="Race", content="Retained", status="published", post_type="notice")
                collected, resume = Event(), Event()
                failures = []
                original_delete = Collector.delete

                def paused_delete(collector):
                    collected.set()
                    if not resume.wait(5):
                        raise AssertionError("Timed out waiting for the scoped reference")
                    return original_delete(collector)

                def delete_scope():
                    close_old_connections()
                    try:
                        type(target).objects.filter(pk=target.pk).delete()
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        connections["default"].close()

                with patch.object(Collector, "delete", paused_delete):
                    worker = Thread(target=delete_scope)
                    worker.start()
                    try:
                        self.assertTrue(collected.wait(5), "Deletion never reached collection")
                        PostMapping.objects.create(post=post, node=node)
                    finally:
                        resume.set()
                        worker.join(10)
                self.assertFalse(worker.is_alive(), "Owned deletion worker did not terminate")
                self.assertEqual(len(failures), 1, failures)
                self.assertIsInstance(failures[0], IntegrityError)
                self.assertTrue(type(target).objects.filter(pk=target.pk).exists())
                self.assertTrue(ScopeNode.objects.filter(pk=node.pk).exists())
                self.assertEqual(list(post.mappings.values_list("node_id", flat=True)), [node.pk])
                self.assertTrue(PostEntity.objects.filter(pk=post.pk, status="published").exists())
