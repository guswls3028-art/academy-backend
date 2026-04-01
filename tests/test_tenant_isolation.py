"""
Tenant Isolation Tests
=======================
Tests verifying that tenant data isolation is absolute:
- Data from tenant 1 is invisible to tenant 2
- Permission classes enforce membership requirements
- Cross-tenant queries return empty results

Requirements:
- SQLite in-memory DB (no PostgreSQL)
- No external services (no AWS, Redis, SQS, R2)
- Deterministic, fast

Run:  pytest tests/test_tenant_isolation.py -v
"""

from unittest.mock import MagicMock

from django.test import TestCase

from apps.core.models import Tenant, TenantMembership, User
from apps.core.models.user import user_internal_username
from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture
from apps.domains.enrollment.models import Enrollment
from apps.domains.community.models import PostEntity, ScopeNode, PostMapping


# ============================================================
# Helper factories
# ============================================================

def _create_tenant(code, name=None):
    return Tenant.objects.create(code=code, name=name or code)


def _create_user(tenant, username_display, password="testpass123"):
    internal_username = user_internal_username(tenant, username_display)
    return User.objects.create_user(
        username=internal_username,
        password=password,
        tenant=tenant,
        name=username_display,
    )


def _create_student(tenant, user, name, ps_number):
    return Student.objects.create(
        tenant=tenant,
        user=user,
        name=name,
        ps_number=ps_number,
        omr_code="12345678",
        parent_phone="01012345678",
        school_type="HIGH",
    )


def _create_lecture(tenant, title):
    return Lecture.objects.create(
        tenant=tenant,
        title=title,
        name=title,
        subject="math",
    )


def _mock_request(user=None, tenant=None):
    """Create a mock request with user and tenant."""
    request = MagicMock()
    request.user = user
    request.tenant = tenant
    if user:
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
    return request


# ============================================================
# Task 2-A: Data Isolation
# ============================================================

class TestDataIsolation(TestCase):
    """Two tenants with one student each; data must not leak."""

    @classmethod
    def setUpTestData(cls):
        # Tenant 1
        cls.tenant1 = _create_tenant("iso-t1", "Academy 1")
        cls.user1 = _create_user(cls.tenant1, "student1")
        cls.student1 = _create_student(cls.tenant1, cls.user1, "Student1", "PS-T1-001")
        TenantMembership.objects.create(
            tenant=cls.tenant1, user=cls.user1, role="student"
        )
        cls.lecture1 = _create_lecture(cls.tenant1, "Lecture T1")
        cls.enrollment1 = Enrollment.objects.create(
            tenant=cls.tenant1, student=cls.student1,
            lecture=cls.lecture1, status="ACTIVE",
        )
        cls.post1 = PostEntity.objects.create(
            tenant=cls.tenant1, post_type="notice",
            title="T1 Notice", content="Tenant 1 only",
        )

        # Tenant 2
        cls.tenant2 = _create_tenant("iso-t2", "Academy 2")
        cls.user2 = _create_user(cls.tenant2, "student2")
        cls.student2 = _create_student(cls.tenant2, cls.user2, "Student2", "PS-T2-001")
        TenantMembership.objects.create(
            tenant=cls.tenant2, user=cls.user2, role="student"
        )
        cls.lecture2 = _create_lecture(cls.tenant2, "Lecture T2")
        cls.enrollment2 = Enrollment.objects.create(
            tenant=cls.tenant2, student=cls.student2,
            lecture=cls.lecture2, status="ACTIVE",
        )
        cls.post2 = PostEntity.objects.create(
            tenant=cls.tenant2, post_type="notice",
            title="T2 Notice", content="Tenant 2 only",
        )

    def test_student_queryset_isolation(self):
        """Students filtered by tenant1 must not include tenant2 students."""
        t1_students = Student.objects.filter(tenant=self.tenant1)
        t1_ids = set(t1_students.values_list("id", flat=True))
        self.assertIn(self.student1.id, t1_ids)
        self.assertNotIn(self.student2.id, t1_ids)

    def test_lecture_queryset_isolation(self):
        """Lectures filtered by tenant1 must not include tenant2 lectures."""
        t1_lectures = Lecture.objects.filter(tenant=self.tenant1)
        t1_ids = set(t1_lectures.values_list("id", flat=True))
        self.assertIn(self.lecture1.id, t1_ids)
        self.assertNotIn(self.lecture2.id, t1_ids)

    def test_post_queryset_isolation(self):
        """Posts filtered by tenant1 must not include tenant2 posts."""
        t1_posts = PostEntity.objects.filter(tenant=self.tenant1)
        t1_ids = set(t1_posts.values_list("id", flat=True))
        self.assertIn(self.post1.id, t1_ids)
        self.assertNotIn(self.post2.id, t1_ids)

    def test_enrollment_isolation(self):
        """Enrollments filtered by tenant must be isolated."""
        t1_enrollments = Enrollment.objects.filter(tenant=self.tenant1)
        t1_ids = set(t1_enrollments.values_list("id", flat=True))
        self.assertIn(self.enrollment1.id, t1_ids)
        self.assertNotIn(self.enrollment2.id, t1_ids)

    def test_membership_isolation(self):
        """TenantMembership must not cross tenants."""
        t1_members = TenantMembership.objects.filter(tenant=self.tenant1)
        t1_user_ids = set(t1_members.values_list("user_id", flat=True))
        self.assertIn(self.user1.id, t1_user_ids)
        self.assertNotIn(self.user2.id, t1_user_ids)


# ============================================================
# Task 2-B: Permission Classes
# ============================================================

class TestPermissionClasses(TestCase):
    """Test TenantResolvedAndMember and TenantResolvedAndStaff."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = _create_tenant("perm-test", "Perm Academy")

        # Student user (with membership)
        cls.student_user = _create_user(cls.tenant, "permstudent")
        cls.student = _create_student(
            cls.tenant, cls.student_user, "PermStudent", "PS-PERM-001"
        )
        TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.student_user, role="student"
        )

        # Staff user (with staff membership)
        cls.staff_user = _create_user(cls.tenant, "permstaff")
        TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.staff_user, role="teacher"
        )

        # Outsider user (no membership in this tenant)
        cls.other_tenant = _create_tenant("perm-other", "Other Academy")
        cls.outsider_user = _create_user(cls.other_tenant, "outsider")

    # --- TenantResolvedAndMember ---

    def test_member_permission_allows_member(self):
        """TenantResolvedAndMember allows user with active membership."""
        perm = TenantResolvedAndMember()
        request = _mock_request(user=self.student_user, tenant=self.tenant)
        # Fast path: user.tenant_id matches tenant.id
        self.assertTrue(perm.has_permission(request, None))

    def test_member_permission_rejects_no_membership(self):
        """TenantResolvedAndMember rejects user without membership in tenant."""
        perm = TenantResolvedAndMember()
        request = _mock_request(user=self.outsider_user, tenant=self.tenant)
        self.assertFalse(perm.has_permission(request, None))

    def test_member_permission_rejects_no_tenant(self):
        """TenantResolvedAndMember rejects when no tenant resolved."""
        perm = TenantResolvedAndMember()
        request = _mock_request(user=self.student_user, tenant=None)
        self.assertFalse(perm.has_permission(request, None))

    def test_member_permission_rejects_unauthenticated(self):
        """TenantResolvedAndMember rejects anonymous user."""
        perm = TenantResolvedAndMember()
        anon = MagicMock()
        anon.is_authenticated = False
        request = _mock_request(user=anon, tenant=self.tenant)
        self.assertFalse(perm.has_permission(request, None))

    # --- TenantResolvedAndStaff ---

    def test_staff_permission_allows_staff(self):
        """TenantResolvedAndStaff allows user with staff role membership."""
        perm = TenantResolvedAndStaff()
        request = _mock_request(user=self.staff_user, tenant=self.tenant)
        # staff_user has teacher role
        self.assertTrue(perm.has_permission(request, None))

    def test_staff_permission_rejects_student(self):
        """TenantResolvedAndStaff rejects student-only user."""
        perm = TenantResolvedAndStaff()
        request = _mock_request(user=self.student_user, tenant=self.tenant)
        # student_user only has student role, not in STAFF_ROLES
        self.assertFalse(perm.has_permission(request, None))

    def test_staff_permission_allows_superuser(self):
        """TenantResolvedAndStaff allows Django superuser regardless of membership."""
        perm = TenantResolvedAndStaff()
        su = User.objects.create_superuser(
            username="superadmin", password="pass123"
        )
        request = _mock_request(user=su, tenant=self.tenant)
        self.assertTrue(perm.has_permission(request, None))

    def test_staff_permission_rejects_no_tenant(self):
        """TenantResolvedAndStaff rejects non-superuser when no tenant."""
        perm = TenantResolvedAndStaff()
        request = _mock_request(user=self.staff_user, tenant=None)
        self.assertFalse(perm.has_permission(request, None))


# ============================================================
# Task 2-C: Cross-Tenant Prevention
# ============================================================

class TestCrossTenantPrevention(TestCase):
    """
    Verify that querying data from one tenant's context
    never returns data belonging to another tenant.
    """

    @classmethod
    def setUpTestData(cls):
        # --- Tenant 1 data ---
        cls.tenant1 = _create_tenant("cross-t1", "Cross Academy 1")
        cls.user1 = _create_user(cls.tenant1, "cross1")
        cls.student1 = _create_student(
            cls.tenant1, cls.user1, "CrossStudent1", "PS-X1-001"
        )
        cls.lecture1 = _create_lecture(cls.tenant1, "Cross Lecture 1")
        cls.enrollment1 = Enrollment.objects.create(
            tenant=cls.tenant1, student=cls.student1,
            lecture=cls.lecture1, status="ACTIVE",
        )
        cls.post1_a = PostEntity.objects.create(
            tenant=cls.tenant1, post_type="notice",
            title="Cross T1 Notice A", content="A",
        )
        cls.post1_b = PostEntity.objects.create(
            tenant=cls.tenant1, post_type="board",
            title="Cross T1 Board B", content="B",
        )

        # --- Tenant 2 (empty context — should see nothing from T1) ---
        cls.tenant2 = _create_tenant("cross-t2", "Cross Academy 2")
        cls.user2 = _create_user(cls.tenant2, "cross2")
        cls.student2 = _create_student(
            cls.tenant2, cls.user2, "CrossStudent2", "PS-X2-001"
        )

    def test_posts_from_other_tenant_empty(self):
        """Querying posts for tenant2 must not return tenant1 posts."""
        t2_posts = PostEntity.objects.filter(tenant=self.tenant2)
        self.assertEqual(t2_posts.count(), 0)

    def test_students_from_other_tenant_empty(self):
        """Querying students for tenant2 must not return tenant1 students."""
        t2_students = Student.objects.filter(tenant=self.tenant2)
        t2_ids = set(t2_students.values_list("id", flat=True))
        self.assertNotIn(self.student1.id, t2_ids)
        self.assertEqual(t2_students.count(), 1)  # only student2

    def test_lectures_from_other_tenant_empty(self):
        """Querying lectures for tenant2 must not return tenant1 lectures."""
        t2_lectures = Lecture.objects.filter(tenant=self.tenant2)
        self.assertEqual(t2_lectures.count(), 0)

    def test_enrollments_from_other_tenant_empty(self):
        """Querying enrollments for tenant2 must not return tenant1 enrollments."""
        t2_enrollments = Enrollment.objects.filter(tenant=self.tenant2)
        self.assertEqual(t2_enrollments.count(), 0)

    def test_cross_tenant_post_filter_ids(self):
        """Filtering tenant2 posts by tenant1 post IDs still returns empty."""
        t1_post_ids = [self.post1_a.id, self.post1_b.id]
        leaked = PostEntity.objects.filter(
            tenant=self.tenant2, id__in=t1_post_ids
        )
        self.assertEqual(leaked.count(), 0)

    def test_cross_tenant_student_filter_ids(self):
        """Filtering tenant2 students by tenant1 student IDs still returns empty."""
        leaked = Student.objects.filter(
            tenant=self.tenant2, id=self.student1.id
        )
        self.assertEqual(leaked.count(), 0)

    def test_cross_tenant_enrollment_filter(self):
        """Filtering tenant2 enrollments by tenant1 enrollment ID returns empty."""
        leaked = Enrollment.objects.filter(
            tenant=self.tenant2, id=self.enrollment1.id
        )
        self.assertEqual(leaked.count(), 0)

    def test_result_service_rejects_cross_tenant(self):
        """
        get_my_exam_result_data with wrong tenant raises Http404,
        even if the user has data in another tenant.
        """
        from unittest.mock import MagicMock
        from django.http import Http404
        from apps.domains.results.services.student_result_service import (
            get_my_exam_result_data,
        )
        from apps.domains.exams.models import Exam, ExamEnrollment

        # Create exam + enrollment in tenant1
        from apps.domains.lectures.models import Session
        session = Session.objects.create(
            lecture=self.lecture1, order=1, title="S1"
        )
        exam = Exam.objects.create(
            tenant=self.tenant1,
            title="Cross Exam", exam_type=Exam.ExamType.REGULAR,
            status=Exam.Status.OPEN, max_score=100, pass_score=60,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment1)

        # user1 requests with tenant2 context => must 404
        request = MagicMock()
        request.user = self.user1
        request.tenant = self.tenant2

        with self.assertRaises(Http404):
            get_my_exam_result_data(request, exam.id, tenant=self.tenant2)
