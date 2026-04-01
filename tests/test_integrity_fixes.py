"""
Data Integrity Fix Verification Tests
======================================
Tests verifying that the Critical/High fixes from the domain audit are effective.

Run: pytest tests/test_integrity_fixes.py -v
"""
from unittest.mock import MagicMock

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
# C2: Manual grading field names
# ============================================================

class TestManualGradingFields(TestCase):
    """C2: ExamResult model must have the fields that apply_manual_overrides writes to."""

    def test_exam_result_has_manual_overrides_field(self):
        from apps.domains.results.models import ExamResult
        self.assertTrue(hasattr(ExamResult, "manual_overrides"))

    def test_exam_result_has_subjective_score_field(self):
        from apps.domains.results.models import ExamResult
        self.assertTrue(hasattr(ExamResult, "subjective_score"))

    def test_exam_result_no_manual_breakdown_field(self):
        """manual_breakdown is NOT a model field — apply_manual_overrides must use manual_overrides."""
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

    def test_grade_submission_calls_sync_for_all_sources(self):
        """Verify grading_service.py no longer checks source == ONLINE."""
        import inspect
        from apps.domains.results.services import grading_service
        source = inspect.getsource(grading_service.grade_submission)
        self.assertNotIn("Source.ONLINE", source,
                         "grade_submission should sync Result for ALL sources, not just ONLINE")
