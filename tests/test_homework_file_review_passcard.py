"""API integration between submission review, score correction and the clinic passcard."""

import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.domains.clinic.test_support import create_clinic_participant_fixture, create_clinic_session_fixture
from apps.domains.enrollment.test_support import create_session_enrollment_fixture
from apps.domains.lectures.test_support import create_session_fixture
from apps.domains.submissions.tests.test_homework_media_submission import HomeworkMediaFixtureMixin, _jpeg


class HomeworkFileReviewPasscardTests(HomeworkMediaFixtureMixin, TestCase):
    def _homework_review_http_fixture(self):
        self.homework.meta = {"default_max_score": 100}
        self.homework.save(update_fields=["meta"])
        headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}
        learner = APIClient()
        learner.force_authenticate(self.student_user)

        def upload(position, body):
            path = f"/api/v1/submissions/submissions/homework/{self.homework.pk}/media/"
            with patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2"):
                return learner.post(path, {
                    "enrollment_id": self.enrollment.pk, "client_file_id": str(uuid.uuid4()),
                    "upload_batch_id": str(uuid.uuid4()), "position": position,
                    "file": _jpeg(body=body),
                }, format="multipart", **headers)

        first = upload(0, b"original")
        self.assertEqual(first.status_code, 201, first.data)
        staff = APIClient()
        staff.force_authenticate(self.teacher)
        editor_headers = {
            **headers,
            "HTTP_X_SCORE_EDITOR_CLIENT": "homework-acceptance-regression",
            "HTTP_X_SCORE_SESSION_ID": str(self.session.pk),
        }
        lease = staff.put(f"/api/v1/results/admin/sessions/{self.session.pk}/score-draft/", {
            "changes": [],
        }, format="json", **editor_headers)
        self.assertEqual(lease.status_code, 200, lease.data)
        score_version = None

        def change_score(value):
            nonlocal score_version
            response = staff.patch("/api/v1/homework/scores/quick/", {
                "session_id": self.session.pk, "homework_id": self.homework.pk, "enrollment_id": self.enrollment.pk,
                "score": value, "max_score": 100, "expected_updated_at": score_version,
            }, format="json", **editor_headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["score"], value)
            self.assertIs(response.data["passed"], value == 90)
            score_version = response.data["updated_at"]

        change_score(90)
        score = django_apps.get_model("homework_results", "HomeworkScore").objects.get(
            session=self.session, homework=self.homework, enrollment=self.enrollment, attempt_index=1,
        )

        def fingerprint():
            response = staff.get(f"/api/v1/submissions/submissions/students/{self.student.pk}/homework/", **headers)
            self.assertEqual(response.status_code, 200, response.data)
            return response.data["results"][0]["media_set_fingerprint"]

        def legacy_review_version():
            response = staff.get(f"/api/v1/submissions/submissions/homework/{self.homework.pk}/", **headers)
            self.assertEqual(response.status_code, 200, response.data)
            return response.data[0]["teacher_review_updated_at"]

        def file_review(accepted, expected_updated_at=None, expected_fingerprint=None):
            return staff.patch(f"/api/v1/submissions/submissions/homework/{self.homework.pk}/acceptance/", {
                "enrollment_id": self.enrollment.pk, "accepted": accepted,
                "expected_updated_at": expected_updated_at,
                "expected_media_set_fingerprint": expected_fingerprint or fingerprint(),
            }, format="json", **headers)

        def manual_review(completed, expected_updated_at=None):
            return staff.patch(f"/api/v1/results/admin/sessions/{self.session.pk}/score-correction/", {
                "enrollment_id": self.enrollment.pk, "source_type": "homework", "source_id": self.homework.pk,
                "completed": completed, "expected_updated_at": expected_updated_at, "note": "수기 판정",
                **({"expected_media_set_fingerprint": fingerprint()} if completed else {}),
            }, format="json", **headers)

        def score_block():
            result = staff.get(f"/api/v1/results/admin/sessions/{self.session.pk}/scores/", **headers)
            self.assertEqual(result.status_code, 200, result.data)
            student_row = next(row for row in result.data["rows"] if row["enrollment_id"] == self.enrollment.pk)
            return next(row["block"] for row in student_row["homeworks"] if row["homework_id"] == self.homework.pk)

        return headers, score, file_review, manual_review, score_block, legacy_review_version, upload, fingerprint, change_score

    def test_file_pass_does_not_create_or_replace_a_manual_review(self):
        from apps.support.progress.session_calculator_dependencies import homework_teacher_approval_passed

        _, score, file_review, manual_review, score_block, legacy_review_version, _, _, change_score = self._homework_review_http_fixture()
        # This legacy approval predates an editor audit row.
        score.teacher_approved = True
        score.save(update_fields=["teacher_approved"])

        def teacher_approval_passed():
            return homework_teacher_approval_passed(enrollment_id=self.enrollment.pk, session=self.session)

        self.assertTrue(teacher_approval_passed())
        baseline = score_block()
        self.assertEqual(baseline["correction_status"], "NOT_REQUIRED")
        self.assertIsNone(baseline["correction_updated_at"])
        accepted = file_review(True)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        read_back = score_block()
        self.assertEqual(read_back["correction_status"], "NOT_REQUIRED")
        self.assertIsNone(read_back["correction_updated_at"])
        self.assertEqual(read_back["score"], 90)
        self.assertTrue(read_back["passed"])
        self.assertIsNone(legacy_review_version())
        self.assertTrue(teacher_approval_passed())
        cancelled = file_review(False, accepted.data["teacher_accepted_updated_at"])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertTrue(teacher_approval_passed())
        accepted = file_review(True, cancelled.data["teacher_accepted_updated_at"])
        self.assertEqual(accepted.status_code, 200, accepted.data)

        manual = manual_review(False)
        self.assertEqual(manual.status_code, 200, manual.data)
        manual_version = manual.data["correction_updated_at"]
        self.assertEqual(manual.data["correction_status"], "PENDING")
        self.assertEqual(legacy_review_version(), manual_version.isoformat())
        django_apps.get_model("progress", "AssessmentCorrection").objects.filter(
            tenant=self.tenant, enrollment=self.enrollment, source_type="homework", source_id=self.homework.pk,
        ).update(updated_by=None)
        self.assertFalse(teacher_approval_passed())
        change_score(0)
        still_accepted = score_block()
        self.assertTrue(still_accepted["passed"])
        self.assertEqual(still_accepted["score"], 0)
        self.assertEqual(datetime.fromisoformat(still_accepted["correction_updated_at"]), manual_version)
        self.assertEqual(file_review(False).status_code, 409)
        cancelled = file_review(False, accepted.data["teacher_accepted_updated_at"])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        after = score_block()
        self.assertEqual(after["score"], 0)
        self.assertFalse(after["passed"])
        self.assertEqual(after["correction_status"], "PENDING")
        self.assertEqual(datetime.fromisoformat(after["correction_updated_at"]), manual_version)
        self.assertEqual(after["correction_note"], "수기 판정")
        self.assertFalse(teacher_approval_passed())
        self.assertEqual(legacy_review_version(), manual_version.isoformat())

    def test_idcard_keeps_scored_pass_and_manual_incomplete_across_file_pass_and_cancel(self):
        from apps.support.clinic import idcard_dependencies

        if not hasattr(idcard_dependencies, "passed_todays_assessments"):
            self.skipTest("Requires the passcard today-assessments contract from #853.")

        headers, score, file_review, manual_review, _, _, upload, fingerprint, change_score = self._homework_review_http_fixture()
        today = timezone.localdate()
        self.session.date = today
        self.session.save(update_fields=["date"])
        django_apps.get_model("progress", "SessionProgress").objects.create(
            enrollment=self.enrollment, session=self.session, exam_attempted=True, exam_passed=True,
            homework_submitted=False, homework_passed=False,
        )
        old_session = create_session_fixture(
            lecture=self.lecture, order=2, title="이전 보강 대상 차시", date=today - timedelta(days=7),
        )
        create_session_enrollment_fixture(tenant=self.tenant, enrollment=self.enrollment, session=old_session)
        exam = django_apps.get_model("exams", "Exam").objects.create(
            tenant=self.tenant, title="이전 보강 대상 시험", exam_type="regular", is_active=True,
        )
        exam.sessions.add(old_session)
        link = django_apps.get_model("progress", "ClinicLink").objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=old_session,
            source_type="exam", source_id=exam.pk, is_auto=True, reason="AUTO_FAILED",
        )
        booking = create_clinic_participant_fixture(
            tenant=self.tenant, student=self.student, enrollment=self.enrollment, status="attended",
            source="student_request", session=create_clinic_session_fixture(
                tenant=self.tenant, date=today - timedelta(days=1), start_time=time(10),
                location="회귀 검증실", max_participants=5,
            ),
        )
        learner = APIClient()
        learner.force_authenticate(self.student_user)

        def assert_card(passed, *, scored_pass=True):
            response = learner.get("/api/v1/clinic/idcard/", **headers)
            self.assertEqual(response.status_code, 200, response.data)
            targets = {target["clinic_link_id"] for target in response.data["current_targets"]}
            self.assertIn(link.pk, targets)
            if scored_pass:
                self.assertEqual(response.data["passcard_state"], "BOOKING_CONFIRMED", response.data)
                self.assertEqual(response.data["current_booking"]["participant_id"], booking.pk)
                self.assertEqual(targets, {link.pk})
            self.assertIs(response.data["today_assessments_passed"], passed)

        assert_card(True)
        accepted = file_review(True)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        assert_card(True)
        cancelled = file_review(False, accepted.data["teacher_accepted_updated_at"])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        assert_card(True)
        locked = upload(1, b"cannot replace a passing score")
        self.assertEqual(locked.status_code, 409, locked.data)
        self.assertEqual(locked.data["code"], "HOMEWORK_MEDIA_REVIEWED")
        # A tutor retracting the passing score makes replacement eligible.
        change_score(0)
        assert_card(False, scored_pass=False)
        old_fingerprint = fingerprint()
        replacement = upload(1, b"new evidence after retraction")
        self.assertEqual(replacement.status_code, 201, replacement.data)
        stale = file_review(True, cancelled.data["teacher_accepted_updated_at"], old_fingerprint)
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(stale.data["code"], "HOMEWORK_MEDIA_CHANGED")
        accepted = file_review(True, cancelled.data["teacher_accepted_updated_at"])
        self.assertEqual(accepted.status_code, 200, accepted.data)
        assert_card(False, scored_pass=False)
        cancelled = file_review(False, accepted.data["teacher_accepted_updated_at"])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        change_score(90)
        assert_card(True)
        manual = manual_review(False)
        self.assertEqual(manual.status_code, 200, manual.data)
        assert_card(False)
        accepted = file_review(True, cancelled.data["teacher_accepted_updated_at"])
        self.assertEqual(accepted.status_code, 200, accepted.data)
        assert_card(False)
        cancelled = file_review(False, accepted.data["teacher_accepted_updated_at"])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        assert_card(False)
        completed = manual_review(True, manual.data["correction_updated_at"].isoformat())
        self.assertEqual(completed.status_code, 200, completed.data)
        assert_card(True)
        score.refresh_from_db()
        self.assertEqual(score.score, 90)
        self.assertTrue(score.passed)
