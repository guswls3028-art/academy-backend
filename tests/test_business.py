"""
Business Logic Tests
=====================
Tests for core business logic: student creation, score/result flow,
and community post visibility scoping.

Requirements:
- SQLite in-memory DB (no PostgreSQL)
- No external services (no AWS, Redis, SQS, R2)
- Deterministic, fast

Run:  pytest tests/test_business.py -v
"""

from django.test import TestCase
from django.db.models import Q

from apps.core.models import Tenant, TenantMembership, User
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.submissions.models.submission import Submission
from apps.domains.community.models import (
    PostEntity,
    PostMapping,
    ScopeNode,
)


# ============================================================
# Helper: reusable factory for creating test data
# ============================================================

def _create_tenant(code="test", name="Test Academy"):
    return Tenant.objects.create(code=code, name=name)


def _create_user(tenant, username_display, password="testpass123"):
    internal_username = user_internal_username(tenant, username_display)
    return User.objects.create_user(
        username=internal_username,
        password=password,
        tenant=tenant,
        name=username_display,
        phone="01012345678",
    )


def _create_student(tenant, user, name, ps_number, parent_phone="01099998888"):
    return Student.objects.create(
        tenant=tenant,
        user=user,
        name=name,
        ps_number=ps_number,
        omr_code=parent_phone[-8:],
        parent_phone=parent_phone,
        school_type="HIGH",
    )


def _create_lecture(tenant, title, name=None):
    return Lecture.objects.create(
        tenant=tenant,
        title=title,
        name=name or title,
        subject="math",
    )


def _create_session(lecture, order=1, title=None):
    return Session.objects.create(
        lecture=lecture,
        order=order,
        title=title or f"{lecture.title} - {order}th",
    )


# ============================================================
# Task 1-A: Student Creation
# ============================================================

class TestStudentCreation(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = _create_tenant(code="stu-test", name="Student Test Academy")
        cls.user = _create_user(cls.tenant, "kim01")
        cls.student = _create_student(
            tenant=cls.tenant,
            user=cls.user,
            name="Kim",
            ps_number="PS-001",
            parent_phone="01011112222",
        )
        cls.membership = TenantMembership.objects.create(
            tenant=cls.tenant,
            user=cls.user,
            role="student",
        )

    def test_student_fields(self):
        """Verify all core fields are set correctly."""
        s = self.student
        self.assertEqual(s.tenant_id, self.tenant.id)
        self.assertEqual(s.user_id, self.user.id)
        self.assertEqual(s.name, "Kim")
        self.assertEqual(s.ps_number, "PS-001")
        self.assertEqual(s.omr_code, "11112222")
        self.assertEqual(s.parent_phone, "01011112222")
        self.assertEqual(s.school_type, "HIGH")

    def test_tenant_membership_created(self):
        """TenantMembership must exist for student."""
        exists = TenantMembership.objects.filter(
            tenant=self.tenant,
            user=self.user,
            role="student",
            is_active=True,
        ).exists()
        self.assertTrue(exists)

    def test_ps_number_generated(self):
        """ps_number must be non-empty."""
        self.assertTrue(self.student.ps_number)
        self.assertEqual(self.student.ps_number, "PS-001")

    def test_user_student_profile_reverse(self):
        """User.student_profile OneToOne reverse relation works."""
        self.assertEqual(self.user.student_profile, self.student)

    def test_ps_number_unique_per_tenant(self):
        """ps_number must be unique within a tenant."""
        user2 = _create_user(self.tenant, "lee01")
        with self.assertRaises(Exception):
            # Should violate uniq_student_ps_number_per_tenant
            _create_student(
                tenant=self.tenant,
                user=user2,
                name="Lee",
                ps_number="PS-001",  # duplicate
            )


# ============================================================
# Task 1-B: Score/Result Flow
# ============================================================

class TestScoreResultFlow(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = _create_tenant(code="score-test", name="Score Test Academy")
        cls.user = _create_user(cls.tenant, "scorestudent")
        cls.student = _create_student(
            cls.tenant, cls.user, "ScoreStudent", "PS-SCORE-001"
        )
        TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.user, role="student"
        )

        # Lecture + Session
        cls.lecture = _create_lecture(cls.tenant, "Math Lecture")
        cls.session = _create_session(cls.lecture, order=1)

        # Enrollment
        cls.enrollment = Enrollment.objects.create(
            tenant=cls.tenant,
            student=cls.student,
            lecture=cls.lecture,
            status="ACTIVE",
        )

        # Exam (regular, linked to session)
        cls.exam = Exam.objects.create(
            title="Mid-Term Math",
            exam_type=Exam.ExamType.REGULAR,
            status=Exam.Status.OPEN,
            max_score=100.0,
            pass_score=60.0,
            allow_retake=False,
            max_attempts=1,
        )
        cls.exam.sessions.add(cls.session)

        # ExamEnrollment
        cls.exam_enrollment = ExamEnrollment.objects.create(
            exam=cls.exam,
            enrollment=cls.enrollment,
        )

        # Submission (required for ExamResult, but we test via Result model)
        cls.submission = Submission.objects.create(
            tenant=cls.tenant,
            user=cls.user,
            enrollment_id=cls.enrollment.id,
            target_type="exam",
            target_id=cls.exam.id,
            source="online",
            status="done",
        )

        # ExamAttempt
        cls.attempt = ExamAttempt.objects.create(
            exam_id=cls.exam.id,
            enrollment_id=cls.enrollment.id,
            submission_id=cls.submission.id,
            attempt_index=1,
            is_representative=True,
            status="done",
        )

        # Result (snapshot)
        cls.result = Result.objects.create(
            target_type="exam",
            target_id=cls.exam.id,
            enrollment_id=cls.enrollment.id,
            attempt_id=cls.attempt.id,
            total_score=85.0,
            max_score=100.0,
            objective_score=85.0,
        )

    def test_result_created_with_correct_scores(self):
        """Result must store scores accurately."""
        r = self.result
        self.assertEqual(r.total_score, 85.0)
        self.assertEqual(r.max_score, 100.0)
        self.assertEqual(r.objective_score, 85.0)
        self.assertEqual(r.target_type, "exam")
        self.assertEqual(r.target_id, self.exam.id)

    def test_result_linked_to_attempt(self):
        """Result.attempt_id must match the ExamAttempt."""
        self.assertEqual(self.result.attempt_id, self.attempt.id)

    def test_result_lookup_by_enrollment(self):
        """Can look up result by enrollment + exam."""
        result = Result.objects.filter(
            target_type="exam",
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
        ).first()
        self.assertIsNotNone(result)
        self.assertEqual(result.total_score, 85.0)

    def test_exam_enrollment_links_exam_and_enrollment(self):
        """ExamEnrollment correctly links Exam to Enrollment."""
        ee = ExamEnrollment.objects.filter(
            exam=self.exam,
            enrollment=self.enrollment,
        ).first()
        self.assertIsNotNone(ee)

    def test_student_result_service(self):
        """get_my_exam_result_data returns correct data for authenticated student."""
        from unittest.mock import MagicMock
        from apps.domains.results.services.student_result_service import (
            get_my_exam_result_data,
        )

        request = MagicMock()
        request.user = self.user
        request.tenant = self.tenant

        data = get_my_exam_result_data(request, self.exam.id, tenant=self.tenant)

        self.assertEqual(data["exam_id"], self.exam.id)
        self.assertEqual(float(data["total_score"]), 85.0)
        self.assertEqual(float(data["max_score"]), 100.0)
        # pass_score=60, score=85 => is_pass=True
        self.assertTrue(data["is_pass"])
        # allow_retake=False => can_retake=False
        self.assertFalse(data["can_retake"])

    def test_student_result_service_404_wrong_user(self):
        """get_my_exam_result_data raises 404 for a user with no enrollment."""
        from unittest.mock import MagicMock
        from django.http import Http404
        from apps.domains.results.services.student_result_service import (
            get_my_exam_result_data,
        )

        other_user = _create_user(self.tenant, "otheruser")
        request = MagicMock()
        request.user = other_user
        request.tenant = self.tenant

        with self.assertRaises(Http404):
            get_my_exam_result_data(request, self.exam.id, tenant=self.tenant)


# ============================================================
# Task 1-C: Community Post Visibility
# ============================================================

class TestCommunityPostVisibility(TestCase):
    """
    Test post visibility scoping logic from _list_by_type:
    - No mappings (global) => student sees it
    - Mapped to enrolled lecture => student sees it
    - Mapped to non-enrolled lecture => student does NOT see it
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = _create_tenant(code="comm-test", name="Community Test")
        cls.user = _create_user(cls.tenant, "commstudent")
        cls.student = _create_student(
            cls.tenant, cls.user, "CommStudent", "PS-COMM-001"
        )
        TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.user, role="student"
        )

        # 2 lectures: student enrolled in lecture_a only
        cls.lecture_a = _create_lecture(cls.tenant, "Lecture A")
        cls.lecture_b = _create_lecture(cls.tenant, "Lecture B")
        cls.session_a = _create_session(cls.lecture_a, order=1)
        cls.session_b = _create_session(cls.lecture_b, order=1)

        cls.enrollment = Enrollment.objects.create(
            tenant=cls.tenant,
            student=cls.student,
            lecture=cls.lecture_a,
            status="ACTIVE",
        )

        # ScopeNodes for lectures
        cls.node_a = ScopeNode.objects.create(
            tenant=cls.tenant,
            level=ScopeNode.Level.COURSE,
            lecture=cls.lecture_a,
        )
        cls.node_b = ScopeNode.objects.create(
            tenant=cls.tenant,
            level=ScopeNode.Level.COURSE,
            lecture=cls.lecture_b,
        )

        # Post 1: global notice (no mappings)
        cls.global_post = PostEntity.objects.create(
            tenant=cls.tenant,
            post_type="notice",
            title="Global Notice",
            content="Everyone should see this.",
        )

        # Post 2: scoped to lecture_a (student enrolled)
        cls.scoped_post_a = PostEntity.objects.create(
            tenant=cls.tenant,
            post_type="notice",
            title="Notice for Lecture A",
            content="Only Lecture A students.",
        )
        PostMapping.objects.create(post=cls.scoped_post_a, node=cls.node_a)

        # Post 3: scoped to lecture_b (student NOT enrolled)
        cls.scoped_post_b = PostEntity.objects.create(
            tenant=cls.tenant,
            post_type="notice",
            title="Notice for Lecture B",
            content="Only Lecture B students.",
        )
        PostMapping.objects.create(post=cls.scoped_post_b, node=cls.node_b)

    def _get_visible_posts(self, student, tenant, post_type="notice"):
        """
        Reproduce the _list_by_type visibility logic inline.
        This tests the business rule directly without HTTP.
        """
        qs = PostEntity.objects.filter(tenant=tenant, post_type=post_type)

        # Student scope filtering (mirrors community/api/views.py _list_by_type)
        enrolled_lecture_ids = set(
            Enrollment.objects.filter(
                tenant=tenant, student=student, status="ACTIVE"
            ).values_list("lecture_id", flat=True)
        )
        visible_node_ids = set(
            ScopeNode.objects.filter(
                tenant=tenant, lecture_id__in=enrolled_lecture_ids
            ).values_list("id", flat=True)
        )
        scoped_post_ids = set(
            PostMapping.objects.filter(
                node_id__in=visible_node_ids
            ).values_list("post_id", flat=True)
        )
        qs = qs.filter(
            Q(mappings__isnull=True) | Q(id__in=scoped_post_ids)
        ).distinct()
        return list(qs)

    def test_global_notice_visible(self):
        """Post with no mappings (global) is visible to any student."""
        posts = self._get_visible_posts(self.student, self.tenant)
        post_ids = [p.id for p in posts]
        self.assertIn(self.global_post.id, post_ids)

    def test_scoped_notice_enrolled_visible(self):
        """Post mapped to an enrolled lecture is visible."""
        posts = self._get_visible_posts(self.student, self.tenant)
        post_ids = [p.id for p in posts]
        self.assertIn(self.scoped_post_a.id, post_ids)

    def test_scoped_notice_not_enrolled_hidden(self):
        """Post mapped to a non-enrolled lecture is NOT visible."""
        posts = self._get_visible_posts(self.student, self.tenant)
        post_ids = [p.id for p in posts]
        self.assertNotIn(self.scoped_post_b.id, post_ids)

    def test_global_notice_count(self):
        """Student should see exactly 2 notices (global + enrolled lecture)."""
        posts = self._get_visible_posts(self.student, self.tenant)
        self.assertEqual(len(posts), 2)
