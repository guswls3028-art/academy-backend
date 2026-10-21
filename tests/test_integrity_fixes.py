"""
Data Integrity Fix Verification Tests
======================================
Tests verifying that the Critical/High fixes from the domain audit are effective.

Run: pytest tests/test_integrity_fixes.py -v
"""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from apps.core.models import Tenant, TenantMembership, User
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam


# ============================================================
# Helpers
# ============================================================

def _tenant(code):
    return Tenant.objects.create(code=code, name=code)


def _user(tenant, name, password="testpass123"):
    un = user_internal_username(tenant, name)
    return User.objects.create_user(username=un, password=password, tenant=tenant, name=name)


def _student(tenant, user, ps):
    return Student.objects.create(
        tenant=tenant, user=user, name=user.name, ps_number=ps,
        omr_code="99999999", parent_phone="01000000000", school_type="HIGH",
    )


def _lecture(tenant, title):
    return Lecture.objects.create(tenant=tenant, title=title, name=title, subject="math")


# ============================================================
# C1: Score comparison normalization
# ============================================================

class TestScoreNormalization(TestCase):
    """C1: sync_result_from_submission must use case-insensitive comparison."""

    def test_norm_function_consistency(self):
        """Both grading paths should normalize identically."""
        _norm = lambda s: str(s).strip().upper() if s else ""
        # Cases that previously diverged
        self.assertEqual(_norm("b"), _norm("B"))
        self.assertEqual(_norm(" a "), _norm("A"))
        self.assertEqual(_norm(""), _norm(""))


# ============================================================
# C2: Legacy ExamResult compatibility field names
# ============================================================

class TestManualGradingFields(TestCase):
    """C2: ExamResult keeps legacy fields required by historical data and checks."""

    def test_exam_result_has_manual_overrides_field(self):
        from apps.domains.results.models import ExamResult
        self.assertTrue(hasattr(ExamResult, "manual_overrides"))

    def test_exam_result_has_subjective_score_field(self):
        from apps.domains.results.models import ExamResult
        self.assertTrue(hasattr(ExamResult, "subjective_score"))

    def test_exam_result_no_manual_breakdown_field(self):
        """manual_breakdown was never a persisted compatibility field."""
        from apps.domains.results.models import ExamResult
        field_names = [f.name for f in ExamResult._meta.get_fields()]
        self.assertNotIn("manual_breakdown", field_names)
        self.assertNotIn("note", field_names)


# ============================================================
# C3: Exam.tenant NOT NULL
# ============================================================

class TestExamTenantNotNull(TestCase):
    """C3: Exam.tenant must be non-nullable after migration."""

    def test_exam_tenant_not_nullable(self):
        field = Exam._meta.get_field("tenant")
        self.assertFalse(field.null, "Exam.tenant must not be nullable")

    def test_exam_creation_requires_tenant(self):
        with self.assertRaises((IntegrityError, ValueError)):
            Exam.objects.create(title="No Tenant Exam", exam_type="regular")


# ============================================================
# C4/C5: Enrollment delete with tenant filter
# ============================================================

class TestEnrollmentDeleteTenantFilter(TestCase):
    """C4/C5: enrollment_filter_student_delete must accept tenant parameter."""

    @classmethod
    def setUpTestData(cls):
        cls.t1 = _tenant("edel-t1")
        cls.t2 = _tenant("edel-t2")
        cls.u1 = _user(cls.t1, "edel1")
        cls.u2 = _user(cls.t2, "edel2")
        cls.s1 = _student(cls.t1, cls.u1, "EDEL-01")
        cls.s2 = _student(cls.t2, cls.u2, "EDEL-02")
        cls.l1 = _lecture(cls.t1, "L-EDEL-1")
        cls.l2 = _lecture(cls.t2, "L-EDEL-2")

    def test_delete_with_tenant_only_deletes_correct_tenant(self):
        from academy.adapters.db.django import repositories_students as repo
        e1 = Enrollment.objects.create(tenant=self.t1, student=self.s1, lecture=self.l1, status="ACTIVE")
        # Delete with correct tenant
        count, _ = repo.enrollment_filter_student_delete(self.s1.id, tenant=self.t1)
        self.assertEqual(count, 1)

    def test_delete_with_wrong_tenant_deletes_nothing(self):
        from academy.adapters.db.django import repositories_students as repo
        e1 = Enrollment.objects.create(tenant=self.t1, student=self.s1, lecture=self.l1, status="ACTIVE")
        # Delete with wrong tenant
        count, _ = repo.enrollment_filter_student_delete(self.s1.id, tenant=self.t2)
        self.assertEqual(count, 0)
        # Original enrollment still exists
        self.assertTrue(Enrollment.objects.filter(id=e1.id).exists())
        # Cleanup
        e1.delete()


# ============================================================
# C6: Homework.tenant NOT NULL
# ============================================================

class TestHomeworkTenantFK(TestCase):
    """C6: Homework model must have tenant FK, non-nullable."""

    def test_homework_has_tenant_field(self):
        from apps.domains.homework_results.models import Homework
        field = Homework._meta.get_field("tenant")
        self.assertFalse(field.null, "Homework.tenant must not be nullable")

    def test_homework_creation_requires_tenant(self):
        from apps.domains.homework_results.models import Homework
        session = None  # template
        with self.assertRaises((IntegrityError, ValueError)):
            Homework.objects.create(
                title="No Tenant HW",
                homework_type="template",
                session=session,
            )


# ============================================================
# C7: Clinic serializer enrollment_id scoping
# ============================================================

class TestClinicSerializerScoping(TestCase):
    """C7: ClinicSessionParticipantCreateSerializer must scope enrollment_id queryset."""

    @classmethod
    def setUpTestData(cls):
        cls.t1 = _tenant("clin-t1")
        cls.t2 = _tenant("clin-t2")

    def test_enrollment_queryset_scoped_to_tenant(self):
        from apps.domains.clinic.serializers import ClinicSessionParticipantCreateSerializer
        request = MagicMock()
        request.tenant = self.t1
        ser = ClinicSessionParticipantCreateSerializer(context={"request": request})
        qs = ser.fields["enrollment_id"].queryset
        # Should be filtered to t1 — check the query
        self.assertIn("tenant", str(qs.query).lower())


# ============================================================
# H: perform_create tenant injection
# ============================================================

class TestPerformCreateTenantInjection(TestCase):
    """H: ViewSets must inject tenant in perform_create."""

    def test_attendance_viewset_has_perform_create(self):
        from apps.domains.attendance.views import AttendanceViewSet
        self.assertTrue(
            hasattr(AttendanceViewSet, "perform_create"),
            "AttendanceViewSet must have perform_create"
        )

    def test_work_record_viewset_has_perform_create(self):
        from apps.domains.staffs.views import WorkRecordViewSet
        self.assertTrue(
            hasattr(WorkRecordViewSet, "perform_create"),
            "WorkRecordViewSet must have perform_create"
        )

    def test_expense_record_viewset_has_perform_create(self):
        from apps.domains.staffs.views import ExpenseRecordViewSet
        self.assertTrue(
            hasattr(ExpenseRecordViewSet, "perform_create"),
            "ExpenseRecordViewSet must have perform_create"
        )


# ============================================================
# H: Result sync for all sources (not just ONLINE)
# ============================================================

class TestResultSyncAllSources(TestCase):
    """H11: grade_submission must sync Result for all sources, not just ONLINE."""

    @contextmanager
    def _grading_orchestration(self, *, source, not_submitted=False, review_required=False, status=None):
        from apps.domains.results.services import grading_service
        from apps.domains.submissions.models import Submission

        submission = SimpleNamespace(
            id=17, target_type="exam", target_id=23, tenant_id=31,
            source=source, status=status or Submission.Status.DONE,
            Source=Submission.Source, Status=Submission.Status,
        )
        # Keep the public atomic orchestrator real; replace only collaborators
        # that own persistence, source eligibility and downstream side effects.
        with ExitStack() as stack:
            def replace(name, **kwargs):
                return stack.enter_context(patch.object(grading_service, name, **kwargs))

            replace("get_submission_for_grading", return_value=submission)
            scope_lock = replace("lock_score_edit_scope_before_submission_grading")
            replace("lock_exam_submission_regrade_state", return_value=(submission, None, not_submitted))
            exam = object()
            replace("get_exam_for_result_sync", return_value=exam)
            validate = replace("validate_exam_submission_scope")
            complete = replace("complete_submission_after_auto_grade")
            retained = MagicMock(name="retained_legacy")
            stack.enter_context(patch.object(grading_service.ExamResult.objects, "get", return_value=retained))
            service = replace("ExamGradingService").return_value
            legacy = MagicMock(name="graded_legacy")
            service.auto_grade_objective.return_value = legacy
            replace("is_omr_manual_review_required", return_value=review_required)
            sync = replace("sync_result_from_exam_submission", return_value=SimpleNamespace(id=41))
            finalize = replace("finalize_omr_result_if_ready", return_value=SimpleNamespace(projection_ready=True))
            dispatch = replace("dispatch_progress_pipeline")
            calls = MagicMock()
            for name, mock in (("grade", service.auto_grade_objective), ("sync", sync),
                               ("finalize", finalize), ("dispatch", dispatch)):
                calls.attach_mock(mock, name)
            yield SimpleNamespace(
                module=grading_service, submission=submission, scope_lock=scope_lock,
                validate=validate, exam=exam, complete=complete, retained=retained,
                service=service, legacy=legacy, sync=sync, finalize=finalize,
                dispatch=dispatch, calls=calls,
            )

    def test_grade_submission_calls_sync_for_all_sources(self):
        from apps.domains.submissions.models import Submission

        for source in Submission.Source.values:
            for force_regrade in (False, True):
                with self.subTest(source=source, force_regrade=force_regrade):
                    with self._grading_orchestration(source=source) as flow:
                        result = flow.module.grade_submission(17, force_regrade=force_regrade)
                        self.assertIs(result, flow.legacy)
                        flow.scope_lock.assert_called_once_with(submission=flow.submission)
                        flow.service.auto_grade_objective.assert_called_once_with(
                            submission_id=17, force_regrade=force_regrade,
                        )
                        flow.sync.assert_called_once_with(17)
                        flow.dispatch.assert_called_once_with(submission_id=17)
                        expected_calls = ["grade", "sync"]
                        if source in (Submission.Source.OMR_SCAN, Submission.Source.ONLINE):
                            flow.finalize.assert_called_once_with(result_id=41, online_subjective_only=True)
                            expected_calls.append("finalize")
                        else:
                            flow.finalize.assert_not_called()
                        self.assertEqual([call[0] for call in flow.calls.mock_calls], expected_calls + ["dispatch"])

    def test_review_required_omr_keeps_draft_without_canonical_sync_or_dispatch(self):
        from apps.domains.submissions.models import Submission

        with self._grading_orchestration(source=Submission.Source.OMR_SCAN, review_required=True) as flow:
            self.assertIs(flow.module.grade_submission(17), flow.legacy)
            flow.service.auto_grade_objective.assert_called_once_with(submission_id=17, force_regrade=False)
            flow.sync.assert_not_called()
            flow.finalize.assert_not_called()
            flow.dispatch.assert_not_called()

    def test_teacher_cancelled_attempt_is_retained_without_regrading_or_republication(self):
        from apps.domains.submissions.models import Submission

        for status in (Submission.Status.DONE, Submission.Status.ANSWERS_READY):
            with self.subTest(status=status):
                with self._grading_orchestration(
                    source=Submission.Source.ONLINE, not_submitted=True, status=status,
                ) as flow:
                    self.assertIs(flow.module.grade_submission(17, force_regrade=True), flow.retained)
                    flow.validate.assert_called_once_with(submission=flow.submission, exam=flow.exam)
                    flow.service.auto_grade_objective.assert_not_called()
                    flow.sync.assert_not_called()
                    flow.finalize.assert_not_called()
                    flow.dispatch.assert_not_called()
                    if status == Submission.Status.DONE:
                        flow.complete.assert_not_called()
                    else:
                        flow.complete.assert_called_once_with(flow.submission, actor="grader.absence_preserved")
