from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.exams.views.exam_view import ExamViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultFact
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class ExamDeleteGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam Delete Guard",
            code="exam-delete-guard",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam-delete-guard-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Delete Guard Lecture",
            name="Delete Guard Lecture",
            subject="MATH",
        )
        student_user = User.objects.create_user(
            username="exam-delete-guard-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Delete Guard Student",
            ps_number="EDG-001",
            omr_code="00000001",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def _create_regular_exam(self, suffix: str) -> Exam:
        return Exam.objects.create(
            tenant=self.tenant,
            title=f"Guarded Regular {suffix}",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )

    def _delete_exam(self, exam: Exam, session_id: int | None = None):
        path = f"/api/v1/exams/{exam.id}/"
        if session_id is not None:
            path = f"{path}?session_id={session_id}"
        request = self.factory.delete(path)
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return ExamViewSet.as_view({"delete": "destroy"})(request, pk=exam.id)

    def _clinic_link_model(self):
        return apps.get_model("progress", "ClinicLink")

    def _session_scores(self, session: Session):
        from importlib import import_module

        SessionScoresView = import_module(
            "apps.domains.results.views.session_scores_view"
        ).SessionScoresView
        request = self.factory.get(
            f"/api/v1/results/admin/sessions/{session.id}/scores/"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return SessionScoresView.as_view()(request, session_id=session.id)

    def test_empty_regular_exam_remains_deletable(self):
        exam = self._create_regular_exam("empty")

        response = self._delete_exam(exam)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())

    def test_regular_exam_with_only_enrollments_remains_deletable(self):
        exam = self._create_regular_exam("enrollments-only")
        ExamEnrollment.objects.create(
            exam=exam,
            enrollment=self.enrollment,
        )

        response = self._delete_exam(exam)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())
        self.assertFalse(ExamEnrollment.objects.filter(exam_id=exam.id).exists())

    def test_session_scoped_delete_unlinks_shared_regular_exam(self):
        session_a = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Tenant A Session",
        )
        lecture_b = Lecture.objects.create(
            tenant=self.tenant,
            title="Delete Guard Lecture B",
            name="Delete Guard Lecture B",
            subject="MATH",
        )
        session_b = Session.objects.create(
            lecture=lecture_b,
            order=1,
            title="Tenant B Session",
        )
        exam = self._create_regular_exam("shared")
        exam.sessions.add(session_a, session_b)
        ExamEnrollment.objects.create(
            exam=exam,
            enrollment=self.enrollment,
        )

        response = self._delete_exam(exam, session_id=session_a.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], "unlinked")
        self.assertTrue(Exam.objects.filter(id=exam.id).exists())
        self.assertFalse(exam.sessions.filter(id=session_a.id).exists())
        self.assertTrue(exam.sessions.filter(id=session_b.id).exists())
        self.assertTrue(ExamEnrollment.objects.filter(exam_id=exam.id).exists())

    def test_session_scoped_delete_unlinks_shared_exam_and_resolves_only_removed_session_clinic_links(self):
        session_a = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Shared Session A",
        )
        lecture_b = Lecture.objects.create(
            tenant=self.tenant,
            title="Delete Guard Lecture Shared B",
            name="Delete Guard Lecture Shared B",
            subject="MATH",
        )
        session_b = Session.objects.create(
            lecture=lecture_b,
            order=1,
            title="Shared Session B",
        )
        ClinicLink = self._clinic_link_model()
        exam = self._create_regular_exam("shared-clinic")
        exam.sessions.add(session_a, session_b)
        link_a = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session_a,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=exam.id,
        )
        link_b = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session_b,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=exam.id,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self._delete_exam(exam, session_id=session_a.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["action"], "unlinked")
        self.assertEqual(response.data["removed_clinic_link_count"], 1)

        link_a.refresh_from_db()
        link_b.refresh_from_db()
        self.assertIsNotNone(link_a.resolved_at)
        self.assertEqual(link_a.resolution_type, ClinicLink.ResolutionType.SOURCE_REMOVED)
        self.assertEqual(link_a.resolution_evidence["reason"], "exam_removed_from_session")
        self.assertIsNone(link_b.resolved_at)
        self.assertIsNone(link_b.resolution_type)

    def test_session_scoped_delete_last_session_deletes_regular_exam(self):
        session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Only Session",
        )
        exam = self._create_regular_exam("last-session")
        exam.sessions.add(session)

        response = self._delete_exam(exam, session_id=session.id)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())

    def test_session_scoped_delete_last_session_clears_exam_clinic_targets(self):
        session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Only Session With Clinic Link",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=session,
            enrollment=self.enrollment,
        )
        Attendance = apps.get_model("attendance", "Attendance")
        Attendance.objects.create(
            tenant=self.tenant,
            session=session,
            enrollment=self.enrollment,
            status="PRESENT",
        )
        ClinicLink = self._clinic_link_model()
        exam = self._create_regular_exam("last-session-clinic")
        exam.sessions.add(session)
        ExamEnrollment.objects.create(
            exam=exam,
            enrollment=self.enrollment,
        )
        source_link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=exam.id,
            meta={"kind": "EXAM_FAILED", "exam_id": exam.id},
        )
        legacy_link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type=None,
            source_id=None,
            meta={"kind": "EXAM_FAILED", "exam_id": exam.id},
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self._delete_exam(exam, session_id=session.id)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())

        source_link.refresh_from_db()
        legacy_link.refresh_from_db()
        for link in (source_link, legacy_link):
            self.assertIsNotNone(link.resolved_at)
            self.assertEqual(link.resolution_type, ClinicLink.ResolutionType.SOURCE_REMOVED)
            self.assertEqual(link.resolution_evidence["source_type"], "exam")
            self.assertEqual(link.resolution_evidence["source_id"], exam.id)
            self.assertEqual(link.resolution_history[-1]["action"], "resolve_source_removed")

        score_response = self._session_scores(session)
        self.assertEqual(score_response.status_code, 200, score_response.data)
        self.assertEqual(score_response.data["meta"]["exams"], [])
        row = score_response.data["rows"][0]
        self.assertFalse(row["clinic_required"])
        self.assertFalse(row["name_highlight_clinic_target"])

    def test_session_scoped_delete_last_session_with_results_archives_regular_exam(self):
        session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Only Session With Results",
        )
        exam = self._create_regular_exam("last-session-results")
        exam.sessions.add(session)
        ExamEnrollment.objects.create(
            exam=exam,
            enrollment=self.enrollment,
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=72,
            max_score=100,
        )
        ClinicLink = self._clinic_link_model()
        clinic_link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=exam.id,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self._delete_exam(exam, session_id=session.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["action"], "archived")
        self.assertEqual(response.data["preserved_blocker"], "results")
        self.assertEqual(response.data["removed_clinic_link_count"], 1)
        self.assertTrue(Exam.objects.filter(id=exam.id).exists())
        self.assertTrue(Result.objects.filter(id=result.id).exists())
        self.assertFalse(exam.sessions.filter(id=session.id).exists())
        self.assertFalse(ExamEnrollment.objects.filter(exam_id=exam.id).exists())

        exam.refresh_from_db()
        self.assertFalse(exam.is_active)
        self.assertEqual(exam.status, Exam.Status.CLOSED)
        clinic_link.refresh_from_db()
        self.assertIsNotNone(clinic_link.resolved_at)
        self.assertEqual(clinic_link.resolution_type, ClinicLink.ResolutionType.SOURCE_REMOVED)

        request = self.factory.get(
            f"/api/v1/exams/?exam_type=regular&session_id={session.id}"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        list_response = ExamViewSet.as_view({"get": "list"})(request)
        rows = (
            list_response.data.get("results", list_response.data)
            if isinstance(list_response.data, dict)
            else list_response.data
        )
        self.assertNotIn(exam.id, {row["id"] for row in rows})

    def test_session_scoped_delete_rejects_unlinked_session(self):
        linked_session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Linked Session",
        )
        unlinked_session = Session.objects.create(
            lecture=self.lecture,
            order=2,
            title="Unlinked Session",
        )
        exam = self._create_regular_exam("wrong-session")
        exam.sessions.add(linked_session)

        response = self._delete_exam(exam, session_id=unlinked_session.id)

        self.assertEqual(response.status_code, 404, response.data)
        self.assertTrue(Exam.objects.filter(id=exam.id).exists())
        self.assertTrue(exam.sessions.filter(id=linked_session.id).exists())

    def test_regular_exam_with_operational_records_cannot_be_deleted(self):
        blockers = (
            (
                "attempt",
                lambda exam: ExamAttempt.objects.create(
                    exam=exam,
                    enrollment=self.enrollment,
                    attempt_index=1,
                ),
            ),
            (
                "submission",
                lambda exam: Submission.objects.create(
                    tenant=self.tenant,
                    user=self.admin,
                    enrollment_id=self.enrollment.id,
                    target_type=Submission.TargetType.EXAM,
                    target_id=exam.id,
                    source=Submission.Source.OMR_SCAN,
                ),
            ),
            (
                "exam result",
                lambda exam: ExamResult.objects.create(
                    submission=Submission.objects.create(
                        tenant=self.tenant,
                        user=self.admin,
                        enrollment_id=self.enrollment.id,
                        target_type=Submission.TargetType.HOMEWORK,
                        target_id=exam.id,
                        source=Submission.Source.HOMEWORK_IMAGE,
                    ),
                    exam=exam,
                ),
            ),
            (
                "result",
                lambda exam: Result.objects.create(
                    target_type="exam",
                    target_id=exam.id,
                    enrollment=self.enrollment,
                    total_score=10,
                    max_score=100,
                ),
            ),
            (
                "result fact",
                lambda exam: ResultFact.objects.create(
                    target_type="exam",
                    target_id=exam.id,
                    enrollment=self.enrollment,
                    submission_id=exam.id,
                    question_id=1,
                    source="manual",
                ),
            ),
        )

        for label, create_child in blockers:
            with self.subTest(label=label):
                exam = self._create_regular_exam(label)
                create_child(exam)

                response = self._delete_exam(exam)

                self.assertEqual(response.status_code, 403, response.data)
                self.assertTrue(Exam.objects.filter(id=exam.id).exists())

    def test_list_filters_by_tenant_exam_type_and_session_id(self):
        other_tenant = Tenant.objects.create(
            name="Other Exam Delete Guard",
            code="exam-delete-guard-other",
            is_active=True,
        )
        lecture_a = self.lecture
        lecture_b = Lecture.objects.create(
            tenant=self.tenant,
            title="Delete Guard Lecture B",
            name="Delete Guard Lecture B",
            subject="MATH",
        )
        other_lecture = Lecture.objects.create(
            tenant=other_tenant,
            title="Other Tenant Lecture",
            name="Other Tenant Lecture",
            subject="MATH",
        )
        session_a = Session.objects.create(
            lecture=lecture_a,
            order=1,
            title="Tenant A Session 1",
        )
        session_b = Session.objects.create(
            lecture=lecture_b,
            order=1,
            title="Tenant A Session 2",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="Other Tenant Session",
        )

        wanted_exam = self._create_regular_exam("wanted")
        wanted_exam.sessions.add(session_a)

        other_session_exam = self._create_regular_exam("other-session")
        other_session_exam.sessions.add(session_b)

        template_exam = Exam.objects.create(
            tenant=self.tenant,
            title="Template Should Be Filtered",
            exam_type=Exam.ExamType.TEMPLATE,
            max_score=100,
            pass_score=60,
        )
        other_tenant_exam = Exam.objects.create(
            tenant=other_tenant,
            title="Other Tenant Exam",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )
        other_tenant_exam.sessions.add(other_session)

        request = self.factory.get(
            f"/api/v1/exams/?exam_type=regular&session_id={session_a.id}"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        rows = (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )
        ids = {row["id"] for row in rows}
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids, {wanted_exam.id})
        self.assertNotIn(other_session_exam.id, ids)
        self.assertNotIn(template_exam.id, ids)
        self.assertNotIn(other_tenant_exam.id, ids)
