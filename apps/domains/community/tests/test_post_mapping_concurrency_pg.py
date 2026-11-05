"""Published audience replacements must remain whole under concurrent editors."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.models import PostEntity, PostMapping, ScopeNode
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class AudienceFixture:
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audience QA", code="qa-audience", is_active=True)
        self.owner = get_user_model().objects.create_user(
            tenant=self.tenant, username="qa-audience-owner", password="test-only",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.courses = []
        self.children = []
        for index in range(3):
            lecture = Lecture.objects.create(
                tenant=self.tenant, name=f"QA course {index}", title=f"QA course {index}",
            )
            session = Session.objects.create(lecture=lecture, order=1, title="QA session")
            self.courses.append(ScopeNode.objects.get(tenant=self.tenant, lecture=lecture, session=None))
            self.children.append(ScopeNode.objects.get(tenant=self.tenant, session=session))
        self.post = PostEntity.objects.create(
            tenant=self.tenant, post_type="notice", title="QA audience notice", content="QA only",
            author_role="staff", status="published",
        )
        PostMapping.objects.create(post=self.post, node=self.courses[0])

    def request(self, method, data=None):
        request = getattr(APIRequestFactory(), method)("/api/v1/community/posts/", data=data, format="json")
        force_authenticate(request, user=self.owner)
        request.tenant = self.tenant
        return request

    def audience(self):
        return set(PostMapping.objects.filter(post=self.post).values_list("node_id", flat=True))


class TestAudienceReplacement(AudienceFixture, TestCase):
    def test_repeat_and_explicit_global_replacement_preserve_contract(self):
        endpoint = PostViewSet.as_view({"patch": "update_nodes"})
        target = [self.courses[1].id, self.children[1].id]
        for _ in range(2):
            response = endpoint(self.request("patch", {"node_ids": target}), pk=self.post.id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.audience(), set(target))
        response = endpoint(self.request("patch", {"node_ids": []}), pk=self.post.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.audience(), set())

    def test_foreign_node_rejection_preserves_saved_audience(self):
        other = Tenant.objects.create(name="Other", code="qa-audience-other", is_active=True)
        lecture = Lecture.objects.create(tenant=other, name="Other", title="Other")
        foreign = ScopeNode.objects.get(tenant=other, lecture=lecture, session=None)
        endpoint = PostViewSet.as_view({"patch": "update_nodes"})
        response = endpoint(self.request("patch", {"node_ids": [foreign.id]}), pk=self.post.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.audience(), {self.courses[0].id})
        foreign_post = PostEntity.objects.create(tenant=other, post_type="notice", title="Other", content="Other")
        response = endpoint(self.request("patch", {"node_ids": [self.courses[1].id]}), pk=foreign_post.id)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PostMapping.objects.filter(post=foreign_post).exists())

class TestConcurrentAudienceReplacement(AudienceFixture, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("Requires PostgreSQL row-lock semantics")
        cls.available_apps = [config.name for config in apps.get_app_configs()]
        super().setUpClass()

    def replace_concurrently(self, first_ids, second_ids):
        snapshot_ready = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_done = threading.Event()
        errors, responses, second_pid = [], [], []
        original_values_list = QuerySet.values_list

        def pause_after_snapshot(queryset, *fields, **kwargs):
            values = original_values_list(queryset, *fields, **kwargs)
            if (queryset.model is PostMapping and fields == ("node_id",)
                    and threading.current_thread().name == "audience-first"
                    and not snapshot_ready.is_set()):
                values = list(values)
                snapshot_ready.set()
                if not release_first.wait(15):
                    raise AssertionError("First editor was never released")
            return values

        def write(ids, second=False):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    if second:
                        second_pid.append(cursor.fetchone()[0])
                if second:
                    second_started.set()
                response = PostViewSet.as_view({"patch": "update_nodes"})(
                    self.request("patch", {"node_ids": ids}), pk=self.post.id,
                )
                responses.append(response.status_code)
            except Exception as exc:
                errors.append(exc)
            finally:
                if second:
                    second_done.set()
                connection.close()

        first = threading.Thread(target=write, args=(first_ids,), name="audience-first")
        second = threading.Thread(target=write, args=(second_ids, True), name="audience-second")
        with patch.object(QuerySet, "values_list", pause_after_snapshot):
            first.start()
            try:
                self.assertTrue(snapshot_ready.wait(15), "First editor did not read saved audience")
                second.start()
                self.assertTrue(second_started.wait(15), "Second editor did not reach the database")
                deadline = time.monotonic() + 10
                while not second_done.is_set() and time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid[0]])
                        if cursor.fetchone()[0]:
                            break
                    time.sleep(0.02)
                else:
                    self.assertTrue(second_done.is_set(), "Second editor neither completed nor waited on a lock")
            finally:
                release_first.set()
                first.join(15)
                if second.ident is not None:
                    second.join(15)
        self.assertFalse(first.is_alive() or second.is_alive(), "An editor did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(responses), [200, 200])
        self.post.refresh_from_db()
        self.assertEqual(self.audience(), set(second_ids))
        self.assertEqual(self.post.title, "QA audience notice")
        response = PostViewSet.as_view({"get": "retrieve"})(self.request("get"), pk=self.post.id)
        self.assertEqual(response.status_code, 200)
        return response

    def test_disjoint_edits_do_not_publish_the_union_of_both_audiences(self):
        self.replace_concurrently([self.courses[1].id], [self.courses[2].id])
        student_model = apps.get_model("students", "Student")
        enrollment_model = apps.get_model("enrollment", "Enrollment")
        for index, expected_status in ((1, 404), (2, 200)):
            user = get_user_model().objects.create_user(
                tenant=self.tenant, username=f"qa-audience-reader-{index}", password="test-only",
            )
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
            student = student_model.objects.create(
                tenant=self.tenant, user=user, ps_number=f"QA{index}", name=f"QA reader {index}",
                parent_phone=f"0100000010{index}", omr_code=f"0000010{index}",
            )
            enrollment_model.objects.create(
                tenant=self.tenant, student=student, lecture_id=self.courses[index].lecture_id,
                status="ACTIVE",
            )
            request = APIRequestFactory().get("/api/v1/community/posts/")
            force_authenticate(request, user=user)
            request.tenant = self.tenant
            response = PostViewSet.as_view({"get": "retrieve"})(request, pk=self.post.id)
            self.assertEqual(response.status_code, expected_status)

    def test_overlapping_edits_do_not_fail_with_a_unique_constraint_error(self):
        self.replace_concurrently([self.courses[1].id], [self.courses[1].id, self.courses[2].id])

    def test_initially_global_post_does_not_merge_two_target_selections(self):
        PostMapping.objects.filter(post=self.post).delete()
        self.replace_concurrently([self.courses[1].id], [self.courses[2].id])
