from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ScoreEditDraft
from apps.domains.results.guards.score_edit_lease_guard import (
    ScoreEditLeaseConflict,
    ScoreEditLeaseStale,
    invalidate_score_edit_leases_for_exam,
    require_score_edit_lease,
)
from apps.domains.results.views.score_draft_view import (
    ScoreDraftCommitView,
    ScoreDraftView,
)


User = get_user_model()
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Exam = apps.get_model("exams", "Exam")


class ScoreDraftEditLeaseTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Score Lease Academy",
            code="score-lease",
            is_active=True,
        )
        self.admin_a = self._staff("score-lease-a")
        self.admin_b = self._staff("score-lease-b")
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Score Lease Lecture",
            name="Score Lease Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="Session 1",
        )

    def _staff(self, username):
        user = User.objects.create_user(
            username=username,
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=user,
            role="admin",
        )
        return user

    def _request(self, method, user, client_id, data=None):
        request = getattr(self.factory, method)(
            "/api/v1/results/admin/sessions/1/score-draft/",
            data or {},
            format="json",
            HTTP_X_SCORE_EDITOR_CLIENT=client_id,
        )
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        request.user = user
        return request

    def _put(
        self,
        user,
        client_id,
        changes=None,
        *,
        session=None,
        acknowledge_stale=False,
    ):
        return ScoreDraftView.as_view()(
            self._request(
                "put",
                user,
                client_id,
                {
                    "changes": changes or [],
                    "acknowledge_stale": acknowledge_stale,
                },
            ),
            session_id=(session or self.session).id,
        )

    def _get(self, user, client_id, *, session=None):
        return ScoreDraftView.as_view()(
            self._request("get", user, client_id),
            session_id=(session or self.session).id,
        )

    def _commit(self, user, client_id, *, release_lease):
        return ScoreDraftCommitView.as_view()(
            self._request(
                "post",
                user,
                client_id,
                {"release_lease": release_lease},
            ),
            session_id=self.session.id,
        )

    def test_active_lease_blocks_other_staff_and_other_tab(self):
        self.assertEqual(self._put(self.admin_a, "tab-a").status_code, 200)

        other_staff = self._put(self.admin_b, "tab-b")
        self.assertEqual(other_staff.status_code, 409)
        self.assertEqual(other_staff.data["code"], "SCORE_EDIT_LOCKED")

        other_tab = self._get(self.admin_a, "tab-a-2")
        self.assertEqual(other_tab.status_code, 409)
        self.assertEqual(other_tab.data["code"], "SCORE_EDIT_LOCKED")

    def test_autosave_commit_keeps_lease_until_explicit_release(self):
        change = {
            "type": "examTotal",
            "examId": 11,
            "enrollmentId": 22,
            "score": 74,
        }
        self.assertEqual(self._put(self.admin_a, "tab-a", [change]).status_code, 200)
        self.assertEqual(
            self._commit(self.admin_a, "tab-a", release_lease=False).status_code,
            204,
        )

        draft = ScoreEditDraft.objects.get(
            session=self.session,
            editor_user=self.admin_a,
        )
        self.assertEqual(draft.payload["changes"], [])
        self.assertEqual(self._put(self.admin_b, "tab-b").status_code, 409)

        self.assertEqual(
            self._commit(self.admin_a, "tab-a", release_lease=True).status_code,
            204,
        )
        self.assertFalse(
            ScoreEditDraft.objects.filter(
                session=self.session,
                editor_user=self.admin_a,
            ).exists()
        )
        self.assertEqual(self._put(self.admin_b, "tab-b").status_code, 200)

    def test_expired_lease_can_be_taken_over(self):
        self.assertEqual(self._put(self.admin_a, "tab-a").status_code, 200)
        ScoreEditDraft.objects.filter(
            session=self.session,
            editor_user=self.admin_a,
        ).update(updated_at=timezone.now() - timedelta(minutes=3))

        self.assertEqual(self._put(self.admin_b, "tab-b").status_code, 200)

    def test_legacy_list_payload_remains_readable(self):
        changes = [{"type": "homework", "enrollmentId": 3, "homeworkId": 4, "score": 5}]
        ScoreEditDraft.objects.create(
            session=self.session,
            tenant=self.tenant,
            editor_user=self.admin_a,
            payload=changes,
        )

        response = self._get(self.admin_a, "tab-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["changes"], changes)

    def test_score_mutation_guard_requires_matching_active_client(self):
        missing = self._request("patch", self.admin_a, "tab-a-2")
        with self.assertRaises(ScoreEditLeaseConflict):
            with transaction.atomic():
                require_score_edit_lease(missing, session_id=self.session.id)

        self.assertEqual(self._put(self.admin_a, "tab-a").status_code, 200)
        matching = self._request("patch", self.admin_a, "tab-a")
        with transaction.atomic():
            self.assertEqual(
                require_score_edit_lease(matching, session_id=self.session.id).id,
                self.session.id,
            )

        other_tab = self._request("patch", self.admin_a, "tab-a-2")
        with self.assertRaises(ScoreEditLeaseConflict):
            with transaction.atomic():
                require_score_edit_lease(other_tab, session_id=self.session.id)

    def test_shared_exam_session_is_one_edit_scope(self):
        sibling = Session.objects.create(
            lecture=self.session.lecture,
            order=2,
            title="Session 2",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Shared Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session, sibling)

        self.assertEqual(self._put(self.admin_a, "tab-a").status_code, 200)

        response = self._put(
            self.admin_b,
            "tab-b",
            session=sibling,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "SCORE_EDIT_LOCKED")

    def test_authoritative_update_preserves_and_stales_manual_draft(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Automatically Graded Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session)
        change = {
            "type": "examTotal",
            "examId": exam.id,
            "enrollmentId": 22,
            "score": 74.5,
        }
        self.assertEqual(self._put(self.admin_a, "tab-a", [change]).status_code, 200)

        with transaction.atomic():
            self.assertEqual(
                invalidate_score_edit_leases_for_exam(
                    exam=exam,
                    tenant=self.tenant,
                    reason="AUTOMATIC_GRADING_COMPLETED",
                ),
                1,
            )

        recovery = self._get(self.admin_a, "tab-a")
        self.assertEqual(recovery.status_code, 200)
        self.assertEqual(recovery.data["changes"], [change])
        self.assertTrue(recovery.data["stale"])

        mutation = self._request("patch", self.admin_a, "tab-a")
        with self.assertRaises(ScoreEditLeaseStale):
            with transaction.atomic():
                require_score_edit_lease(
                    mutation,
                    session_id=self.session.id,
                    exam_id=exam.id,
                )

        rejected = self._put(self.admin_a, "tab-a", [change])
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.data["code"], "SCORE_EDIT_STALE")
        empty_heartbeat = self._put(self.admin_a, "tab-a")
        self.assertEqual(empty_heartbeat.status_code, 409)
        self.assertEqual(empty_heartbeat.data["code"], "SCORE_EDIT_STALE")
        stale_commit = self._commit(
            self.admin_a,
            "tab-a",
            release_lease=False,
        )
        self.assertEqual(stale_commit.status_code, 409)
        self.assertEqual(stale_commit.data["code"], "SCORE_EDIT_STALE")

        restored = self._put(
            self.admin_a,
            "tab-a",
            [change],
            acknowledge_stale=True,
        )
        self.assertEqual(restored.status_code, 200)
