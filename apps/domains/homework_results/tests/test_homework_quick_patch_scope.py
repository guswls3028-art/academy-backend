from concurrent.futures import ThreadPoolExecutor
import uuid
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.views.homework_score_viewset import HomeworkScoreViewSet
from apps.domains.homework_results.views.homework_view import HomeworkViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_remediation_service import ClinicRemediationService
from apps.domains.students.models import Student
from apps.support.submissions.dependencies import homework_submission_revisions


User = get_user_model()
ScoreEditDraft = apps.get_model("results", "ScoreEditDraft")
Submission = apps.get_model("submissions", "Submission")
SubmissionMedia = apps.get_model("submissions", "SubmissionMedia")


class HomeworkQuickPatchScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Homework Scope",
            code="hwscope",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="hwscope-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회차")
        self.other_session = Session.objects.create(lecture=self.lecture, order=2, title="2회차")
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Daily homework",
        )
        self.assigned_enrollment = self._create_enrollment("assigned")
        self.unassigned_enrollment = self._create_enrollment("unassigned")
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
        )
        ScoreEditDraft.objects.create(
            session=self.session,
            tenant=self.tenant,
            editor_user=self.admin,
            payload={"client_id": "test-score-tab", "changes": []},
        )
        ScoreEditDraft.objects.create(
            session=self.other_session,
            tenant=self.tenant,
            editor_user=self.admin,
            payload={"client_id": "test-score-tab", "changes": []},
        )

    def _create_enrollment(self, suffix: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"hwscope-{suffix}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Student {suffix}",
            ps_number=f"HW-{suffix}",
            omr_code=f"HW{suffix.upper()}"[:8],
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=student,
            status="ACTIVE",
        )

    def _quick_patch(self, data, *, expected_status=200):
        request = self.factory.patch(
            "/homework/scores/quick/",
            data,
            format="json",
            HTTP_X_SCORE_EDITOR_CLIENT="test-score-tab",
            HTTP_X_SCORE_SESSION_ID=str(data.get("session_id") or ""),
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkScoreViewSet.as_view({"patch": "quick_patch"})(request)
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def _partial_update(self, score, data, *, expected_status=200):
        request = self.factory.patch(
            f"/homework/scores/{score.id}/",
            data,
            format="json",
            HTTP_X_SCORE_EDITOR_CLIENT="test-score-tab",
            HTTP_X_SCORE_SESSION_ID=str(score.session_id),
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkScoreViewSet.as_view({"patch": "partial_update"})(request, pk=score.id)
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def test_rejects_unassigned_enrollment_without_side_effects(self):
        self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.unassigned_enrollment.id,
                "score": 80,
            },
            expected_status=400,
        )

        self.assertFalse(
            HomeworkScore.objects.filter(
                homework=self.homework,
                enrollment=self.unassigned_enrollment,
            ).exists()
        )

    def test_score_inputs_reject_nonfinite_boolean_and_negative_without_changes(self):
        score = HomeworkScore.objects.create(
            homework=self.homework, session=self.session,
            enrollment=self.assigned_enrollment, attempt_index=1,
            score=80, max_score=100, passed=True, clinic_required=False,
        )
        before = HomeworkScore.objects.filter(pk=score.pk).values().get()
        target = {
            "session_id": self.session.id, "homework_id": self.homework.id,
            "enrollment_id": self.assigned_enrollment.id,
        }
        for action in ("quick", "detail"):
            for field in ("score", "max_score"):
                for invalid in ("NaN", "Infinity", "-Infinity", "1e999", -1, True, False):
                    with self.subTest(action=action, field=field, invalid=invalid):
                        payload = {"score": 80, field: invalid}
                        if action == "quick":
                            response = self._quick_patch({**target, **payload}, expected_status=400)
                        else:
                            response = self._partial_update(score, payload, expected_status=400)
                        self.assertIn(field, response.data)
                        self.assertEqual(HomeworkScore.objects.filter(pk=score.pk).values().get(), before)

    def test_invalid_new_score_creates_no_row_then_corrected_input_succeeds(self):
        target = {
            "session_id": self.session.id, "homework_id": self.homework.id,
            "enrollment_id": self.assigned_enrollment.id,
        }
        self._quick_patch({**target, "score": "NaN"}, expected_status=400)
        self.assertFalse(HomeworkScore.objects.exists())
        response = self._quick_patch({**target, "score": "87.5"})
        score = HomeworkScore.objects.get(pk=response.data["id"])
        self.assertEqual((score.score, score.max_score), (87.5, 100))
        self.assertEqual(HomeworkScore.objects.count(), 1)

    def test_both_score_inputs_preserve_zero_decimal_and_ungraded(self):
        target = {
            "session_id": self.session.id, "homework_id": self.homework.id,
            "enrollment_id": self.assigned_enrollment.id,
        }
        for value in (0, "87.5", 100, None):
            with self.subTest(action="quick", value=value):
                response = self._quick_patch({**target, "score": value})
                score = HomeworkScore.objects.get(pk=response.data["id"])
                expected = float(value) if value is not None else None
                self.assertEqual(score.score, expected)
                self.assertEqual(response.data["score"], expected)
        for value in (0, "87.5", 100, None):
            with self.subTest(action="detail", value=value):
                response = self._partial_update(score, {"score": value})
                score.refresh_from_db()
                expected = float(value) if value is not None else None
                self.assertEqual(score.score, expected)
                self.assertEqual(response.data["score"], expected)
        self.assertEqual(HomeworkScore.objects.count(), 1)

    def test_updates_existing_legacy_score_without_assignment(self):
        legacy_score = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.unassigned_enrollment,
            attempt_index=1,
            score=None,
            max_score=None,
        )

        response = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.unassigned_enrollment.id,
                "score": 80,
            }
        )

        legacy_score.refresh_from_db()
        self.assertEqual(response.data["id"], legacy_score.id)
        self.assertEqual(legacy_score.score, 80)
        self.assertEqual(
            HomeworkScore.objects.filter(
                homework=self.homework,
                session=self.session,
                enrollment=self.unassigned_enrollment,
                attempt_index=1,
            ).count(),
            1,
        )

    def test_rejects_session_mismatch_without_side_effects(self):
        self._quick_patch(
            {
                "session_id": self.other_session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 80,
            },
            expected_status=400,
        )

        self.assertFalse(
            HomeworkScore.objects.filter(
                homework=self.homework,
                enrollment=self.assigned_enrollment,
            ).exists()
        )

    def test_accepts_assigned_enrollment_for_homework_session(self):
        response = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 80,
            }
        )

        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertEqual(response.data["id"], score.id)
        self.assertEqual(score.score, 80)
        self.assertEqual(score.reviewed_submission_revision, "")

    def test_explicit_score_review_captures_current_submission_revision(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.assigned_enrollment.student.user,
            enrollment=self.assigned_enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            status=Submission.Status.SUBMITTED,
        )
        media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            fingerprint="a" * 64,
            object_key="tenant/test/graded.jpg",
            original_filename="풀이.jpg",
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=100,
            position=0,
            status=SubmissionMedia.Status.UPLOADED,
        )
        self._quick_patch({
            "session_id": self.session.id,
            "homework_id": self.homework.id,
            "enrollment_id": self.assigned_enrollment.id,
            "score": 30,
        })
        score = HomeworkScore.objects.get(homework=self.homework, enrollment=self.assigned_enrollment)
        reviewed = homework_submission_revisions(
            tenant=self.tenant,
            enrollment_ids=[self.assigned_enrollment.id],
            homework_ids=[self.homework.id],
        )[(self.assigned_enrollment.id, self.homework.id)]
        self.assertEqual(score.reviewed_submission_revision, reviewed)
        self._quick_patch({
            "session_id": self.session.id,
            "homework_id": self.homework.id,
            "enrollment_id": self.assigned_enrollment.id,
            "meta_status": HomeworkScore.MetaStatus.NOT_SUBMITTED,
        })
        score.refresh_from_db()
        self.assertEqual(score.reviewed_submission_revision, reviewed)
        media.removed_at = score.updated_at
        media.status = SubmissionMedia.Status.REMOVED
        media.save(update_fields=["removed_at", "status"])
        self.assertEqual(
            homework_submission_revisions(
                tenant=self.tenant,
                enrollment_ids=[self.assigned_enrollment.id],
                homework_ids=[self.homework.id],
            ),
            {},
        )

    def test_quick_patch_uses_cell_version_cas_and_preserves_server_value_on_conflict(self):
        first = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 70,
                "expected_updated_at": None,
            }
        )
        original_version = first.data["updated_at"]

        winner = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 80,
                "expected_updated_at": original_version,
            }
        )
        self.assertNotEqual(winner.data["updated_at"], original_version)

        conflict = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 90,
                "expected_updated_at": original_version,
            },
            expected_status=409,
        )

        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertEqual(score.score, 80)
        self.assertEqual(conflict.data["code"], "SCORE_CELL_CONFLICT")
        self.assertEqual(conflict.data["server_value"]["score"], 80)
        self.assertEqual(
            conflict.data["server_value"]["updated_at"],
            winner.data["updated_at"],
        )
        self.assertEqual(conflict.data["expected_updated_at"], original_version)

    def test_quick_patch_expected_empty_cell_conflicts_with_existing_score(self):
        existing = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
            score=65,
            max_score=100,
        )

        conflict = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 75,
                "expected_updated_at": None,
            },
            expected_status=409,
        )

        existing.refresh_from_db()
        self.assertEqual(existing.score, 65)
        self.assertEqual(conflict.data["server_value"]["score"], 65)

    def test_uses_configured_homework_max_score_when_request_omits_it(self):
        self.homework.meta = {"default_max_score": 43}
        self.homework.save(update_fields=["meta", "updated_at"])

        response = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 41,
            }
        )

        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertEqual(response.data["max_score"], 43.0)
        self.assertEqual(score.max_score, 43.0)

    def test_quick_patch_uses_homework_specific_cutline(self):
        self.homework.cutline_mode = Homework.CutlineMode.PERCENT
        self.homework.cutline_value = 70
        self.homework.round_unit_percent = 5
        self.homework.save(
            update_fields=[
                "cutline_mode",
                "cutline_value",
                "round_unit_percent",
                "updated_at",
            ]
        )

        self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 75,
            }
        )

        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertTrue(score.passed)
        self.assertFalse(score.clinic_required)

    def test_rejects_score_above_configured_homework_max_score(self):
        self.homework.meta = {"default_max_score": 43}
        self.homework.save(update_fields=["meta", "updated_at"])

        self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 44,
            },
            expected_status=400,
        )

        self.assertFalse(
            HomeworkScore.objects.filter(
                homework=self.homework,
                enrollment=self.assigned_enrollment,
            ).exists()
        )

    def test_completion_homework_accepts_only_incomplete_or_complete(self):
        self.homework.grading_mode = Homework.GradingMode.COMPLETION
        self.homework.save(update_fields=["grading_mode", "updated_at"])

        incomplete = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 0,
            }
        )
        self.assertEqual(incomplete.data["max_score"], 1.0)
        self.assertFalse(incomplete.data["passed"])

        complete = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 1,
            }
        )
        self.assertEqual(complete.data["score"], 1.0)
        self.assertTrue(complete.data["passed"])

        self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 0.5,
            },
            expected_status=400,
        )
        score = HomeworkScore.objects.get(
            homework=self.homework,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertEqual(score.score, 1.0)

    def test_partial_update_does_not_move_score_relationships(self):
        score = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
            score=70,
        )

        self._partial_update(
            score,
            {
                "session": self.other_session.id,
                "homework": self.homework.id + 9999,
                "score": 75,
            },
        )

        score.refresh_from_db()
        self.assertEqual(score.session_id, self.session.id)
        self.assertEqual(score.homework_id, self.homework.id)
        self.assertEqual(score.score, 75)

    def test_partial_update_status_not_submitted_persists_meta_and_clears_score(self):
        score = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
            score=70,
            max_score=100,
        )

        self._partial_update(score, {"status": HomeworkScore.MetaStatus.NOT_SUBMITTED})

        score.refresh_from_db()
        self.assertIsNone(score.score)
        self.assertIsNone(score.max_score)
        self.assertEqual(score.meta, {"status": HomeworkScore.MetaStatus.NOT_SUBMITTED})

    def test_partial_update_status_null_clears_not_submitted_meta(self):
        score = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
            score=None,
            max_score=None,
            meta={"status": HomeworkScore.MetaStatus.NOT_SUBMITTED},
        )

        self._partial_update(score, {"status": None, "score": 55})

        score.refresh_from_db()
        self.assertEqual(score.score, 55)
        self.assertIsNone(score.meta)


    def test_clinic_first_score_obeys_completion_policy_and_preserves_success(self):
        self.homework.grading_mode = "COMPLETION"
        self.homework.meta = {"default_max_score": 1}
        self.homework.cutline_mode = "COUNT"
        self.homework.cutline_value = 1
        self.homework.save()
        link = ClinicLink.objects.create(
            tenant=self.tenant, session=self.session, enrollment=self.assigned_enrollment,
            source_type="homework", source_id=self.homework.id, reason="AUTO_FAILED",
        )
        with self.assertRaises(ValueError):
            ClinicRemediationService.submit_homework_retake(
                clinic_link_id=link.id, score=85, graded_by_user_id=self.admin.id,
            )
        self.assertFalse(HomeworkScore.objects.filter(homework=self.homework).exists())
        result = ClinicRemediationService.submit_homework_retake(
            clinic_link_id=link.id, score=1, max_score=100, graded_by_user_id=self.admin.id,
        )
        self.assertEqual((result.attempt_index, result.score, result.max_score), (1, 1, 1))
        saved = HomeworkScore.objects.get(homework=self.homework)
        self.assertEqual((saved.score, saved.max_score), (1, 1))
        link.refresh_from_db()
        self.assertIsNotNone(link.resolved_at)


class HomeworkQuickPatchCreateRaceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="CAS Race", code="casrace", is_active=True)
        self.admin = User.objects.create_user(
            username="casrace-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="CAS Race Lecture",
            name="CAS Race Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회차")
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="CAS Race Homework",
        )
        student_user = User.objects.create_user(
            username="casrace-student",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="CAS Race Student",
            ps_number="CAS-RACE",
            omr_code="CASRACE",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=student,
            status="ACTIVE",
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        ScoreEditDraft.objects.create(
            session=self.session,
            tenant=self.tenant,
            editor_user=self.admin,
            payload={"client_id": "cas-race-tab", "changes": []},
        )

    def _request(self, score):
        close_old_connections()
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        user = User.objects.get(pk=self.admin.pk)
        request = self.factory.patch(
            "/homework/scores/quick/",
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.enrollment.id,
                "score": score,
                "expected_updated_at": None,
            },
            format="json",
            HTTP_X_SCORE_EDITOR_CLIENT="cas-race-tab",
            HTTP_X_SCORE_SESSION_ID=str(self.session.id),
        )
        request.tenant = tenant
        force_authenticate(request, user=user)
        try:
            response = HomeworkScoreViewSet.as_view({"patch": "quick_patch"})(request)
            return response.status_code, dict(response.data)
        finally:
            close_old_connections()

    @skipUnless(connection.vendor == "postgresql", "unique create race requires PostgreSQL")
    def test_expected_empty_cell_create_race_allows_one_winner_and_one_conflict(self):
        # The lease contract is covered separately. The canonical assignment lock
        # now serializes the empty-cell create window itself, so forcing both calls
        # to pause inside create would deadlock the test behind that intended lock.
        with patch(
            "apps.domains.homework_results.views.homework_score_viewset."
            "require_homework_score_edit_lease"
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(self._request, [71, 82]))

        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        winner = next(data for status, data in results if status == 200)
        conflict = next(data for status, data in results if status == 409)
        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
            attempt_index=1,
        )
        self.assertEqual(score.score, winner["score"])
        self.assertEqual(conflict["code"], "SCORE_CELL_CONFLICT")
        self.assertEqual(conflict["server_value"]["score"], winner["score"])
        self.assertEqual(HomeworkScore.objects.count(), 1)


    def _mode_request(self):
        request = self.factory.patch(
            f"/homeworks/{self.homework.id}/",
            {"grading_mode": "COMPLETION", "max_score": 1}, format="json",
        )
        request.tenant = Tenant.objects.get(pk=self.tenant.pk)
        force_authenticate(request, user=User.objects.get(pk=self.admin.pk))
        response = HomeworkViewSet.as_view({"patch": "partial_update"})(request, pk=self.homework.id)
        return response.status_code, dict(response.data)

    def _assert_mode_score_serialization(self, *, score_first, clinic=False):
        """Pause the actual first Homework lock, then prove the competing API waits in PostgreSQL."""
        acquired, release, contender_started = Event(), Event(), Event()
        pids = {}
        table = Homework._meta.db_table
        clinic_link = None
        if clinic:
            clinic_link = ClinicLink.objects.create(
                tenant=self.tenant, session=self.session, enrollment=self.enrollment,
                source_type="homework", source_id=self.homework.id, reason="AUTO_FAILED",
            )

        def invoke(label, first):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[label] = cursor.fetchone()[0]
                    cursor.execute("SET lock_timeout = '8s'")
                observed = False

                def observe(execute, sql, params, many, context):
                    nonlocal observed
                    target = not observed and sql.lstrip().upper().startswith("SELECT") and table in sql and ("FOR UPDATE" in sql.upper() or "FOR NO KEY UPDATE" in sql.upper())
                    if target:
                        observed = True
                        if not first:
                            contender_started.set()
                    result = execute(sql, params, many, context)
                    if target and first:
                        acquired.set()
                        if not release.wait(8):
                            raise AssertionError("Timed out releasing the first Homework lock")
                    return result

                with connection.execute_wrapper(observe):
                    if label == "mode":
                        return self._mode_request()
                    if clinic:
                        try:
                            result = ClinicRemediationService.submit_homework_retake(
                                clinic_link_id=clinic_link.id, score=85, graded_by_user_id=self.admin.id,
                            )
                            return 200, {"score": result.score, "max_score": result.max_score}
                        except ValueError as error:
                            return 400, {"detail": str(error)}
                    # Keep this thread's connection/PID through the whole API call.
                    request = self.factory.patch(
                        "/homework/scores/quick/",
                        {"session_id": self.session.id, "homework_id": self.homework.id,
                         "enrollment_id": self.enrollment.id, "score": 85, "expected_updated_at": None},
                        format="json", HTTP_X_SCORE_EDITOR_CLIENT="cas-race-tab",
                        HTTP_X_SCORE_SESSION_ID=str(self.session.id),
                    )
                    request.tenant = Tenant.objects.get(pk=self.tenant.pk)
                    force_authenticate(request, user=User.objects.get(pk=self.admin.pk))
                    response = HomeworkScoreViewSet.as_view({"patch": "quick_patch"})(request)
                    return response.status_code, dict(response.data)
            finally:
                connection.close()

        first = "score" if score_first else "mode"
        second = "mode" if score_first else "score"
        with patch("apps.domains.homework_results.views.homework_score_viewset.schedule_homework_score_progress"), \
                patch("apps.domains.homework_results.views.homework_score_viewset.sync_homework_clinic_link"), \
                patch("apps.domains.progress.services.clinic_resolution_service._dispatch_progress_for_link"), \
                patch("apps.domains.progress.services.clinic_remediation_service._dispatch_progress_for_link"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(invoke, first, True)
                try:
                    self.assertTrue(acquired.wait(5), "first API must lock Homework before any score is created")
                    contender = executor.submit(invoke, second, False)
                    self.assertTrue(contender_started.wait(5), "second API must use the same Homework-first lock order")
                    deadline = monotonic() + 5
                    blocked = False
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [pids[first], pids[second]])
                            blocked = cursor.fetchone()[0]
                        if blocked:
                            break
                        sleep(0.01)
                    self.assertTrue(blocked, "competing API must actually wait on the first Homework row lock")
                finally:
                    release.set()
                results = {first: holder.result(timeout=10), second: contender.result(timeout=10)}
        self.homework.refresh_from_db()
        if score_first:
            self.assertEqual(results["score"][0], 200, results)
            self.assertEqual(results["mode"][0], 400, results)
            self.assertEqual(self.homework.grading_mode, "SCORE")
            saved = HomeworkScore.objects.get(homework=self.homework)
            self.assertEqual((saved.score, saved.max_score), (85, 100))
        else:
            self.assertEqual(results["mode"][0], 200, results)
            self.assertEqual(results["score"][0], 400, results)
            self.assertEqual(self.homework.grading_mode, "COMPLETION")
            self.assertEqual(self.homework.default_max_score, 1)
            self.assertFalse(HomeworkScore.objects.filter(homework=self.homework).exists())

    @skipUnless(connection.vendor == "postgresql", "row-lock serialization requires PostgreSQL")
    def test_first_score_blocks_mode_change_and_preserves_numeric_score(self):
        self._assert_mode_score_serialization(score_first=True)

    @skipUnless(connection.vendor == "postgresql", "row-lock serialization requires PostgreSQL")
    def test_mode_change_blocks_first_score_and_revalidates_new_completion_policy(self):
        self._assert_mode_score_serialization(score_first=False)


    @skipUnless(connection.vendor == "postgresql", "row-lock serialization requires PostgreSQL")
    def test_clinic_first_score_blocks_mode_change(self):
        self._assert_mode_score_serialization(score_first=True, clinic=True)

    @skipUnless(connection.vendor == "postgresql", "row-lock serialization requires PostgreSQL")
    def test_mode_change_blocks_clinic_first_score_and_revalidates_completion(self):
        self._assert_mode_score_serialization(score_first=False, clinic=True)

    @skipUnless(connection.vendor == "postgresql", "FK lock compatibility requires PostgreSQL")
    def test_quick_parent_lock_allows_concurrent_child_fk_commit(self):
        acquired, release = Event(), Event()
        table = Homework._meta.db_table

        def hold_quick():
            close_old_connections()
            observed = False

            def pause(execute, sql, params, many, context):
                nonlocal observed
                target = not observed and table in sql and "FOR NO KEY UPDATE" in sql.upper()
                result = execute(sql, params, many, context)
                if target:
                    observed = True
                    acquired.set()
                    if not release.wait(15):
                        raise AssertionError("Timed out releasing Homework policy lock")
                return result

            try:
                with connection.execute_wrapper(pause):
                    return self._request(85)
            finally:
                connection.close()

        def child_writer():
            close_old_connections()
            try:
                # Represents the FK check of a concurrent remediation/evidence writer.
                # It must commit before the parent policy lock is released.
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    row = HomeworkScore.objects.create(
                        homework_id=self.homework.id, session_id=self.session.id,
                        enrollment_id=self.enrollment.id, attempt_index=2, score=30, max_score=100,
                    )
                return row.id
            finally:
                connection.close()

        with patch("apps.domains.homework_results.views.homework_score_viewset.require_homework_score_edit_lease"), \
                patch("apps.domains.homework_results.views.homework_score_viewset.schedule_homework_score_progress"), \
                patch("apps.domains.homework_results.views.homework_score_viewset.sync_homework_clinic_link"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(hold_quick)
                try:
                    self.assertTrue(acquired.wait(5))
                    child_id = executor.submit(child_writer).result(timeout=8)
                    self.assertTrue(HomeworkScore.objects.filter(pk=child_id).exists())
                finally:
                    release.set()
                self.assertEqual(holder.result(timeout=10)[0], 200)
        self.assertEqual(HomeworkScore.objects.filter(homework=self.homework).count(), 2)
