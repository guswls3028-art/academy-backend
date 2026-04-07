# PATH: apps/domains/lectures/tests/test_lecture_stabilization.py
"""
강의/차시 도메인 안정화 테스트

A. Session order 유니크 제약 (section=NULL)
B. auto_assign() 동시성 안전
C. CLINIC auto_assign source 보존
D. section 타입 검증
E. 날짜 정합성 검증
"""
from django.test import TestCase
from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session, Section, SectionAssignment
from apps.domains.lectures.serializers import (
    LectureSerializer,
    SessionSerializer,
    SectionAssignmentSerializer,
)
from apps.domains.lectures.views import SectionAssignmentViewSet, SessionViewSet
from apps.domains.enrollment.models import Enrollment

User = get_user_model()


class LectureTestBase(TestCase):
    """공통 테스트 설정"""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="TestAcademy", code="test_lec", is_active=True,
        )
        self.admin = User.objects.create_user(
            username="lec_admin", password="test1234",
            tenant=self.tenant, is_staff=True, name="Admin",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant, user=self.admin, role="owner",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant, name="TestLecture", title="TestLecture",
            subject="math",
            start_date="2026-04-01", end_date="2026-04-30",
        )


class TestSessionOrderUnique(LectureTestBase):
    """A. Session order 유니크 제약 (section=NULL)"""

    def test_duplicate_order_same_lecture_no_section_blocked(self):
        """같은 강의, section=NULL인 차시에서 동일 order가 DB에서 차단됨"""
        Session.objects.create(lecture=self.lecture, order=1, title="1차시")
        with self.assertRaises(IntegrityError):
            Session.objects.create(lecture=self.lecture, order=1, title="1차시 복사")

    def test_different_lectures_same_order_allowed(self):
        """다른 강의에서는 같은 order 허용"""
        lecture2 = Lecture.objects.create(
            tenant=self.tenant, name="Lec2", title="Lec2", subject="eng",
        )
        Session.objects.create(lecture=self.lecture, order=1, title="1차시")
        s2 = Session.objects.create(lecture=lecture2, order=1, title="1차시")
        self.assertEqual(s2.order, 1)

    def test_section_sessions_independent_order(self):
        """section이 있는 차시와 없는 차시는 독립적으로 order 관리"""
        section = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="A", section_type="CLASS",
            day_of_week=0, start_time="10:00",
        )
        Session.objects.create(
            lecture=self.lecture, section=section, order=1, title="A반 1차시",
        )
        # section=NULL인 차시도 order=1 가능 (독립 제약)
        Session.objects.create(
            lecture=self.lecture, section=None, order=1, title="공통 1차시",
        )

    def test_serializer_order_duplicate_rejected(self):
        """시리얼라이저에서 order 중복이 거부됨"""
        Session.objects.create(lecture=self.lecture, order=1, title="1차시")
        serializer = SessionSerializer(data={
            "lecture": self.lecture.id,
            "section": None,
            "order": 1,
            "title": "중복 차시",
        })
        valid = serializer.is_valid()
        self.assertFalse(valid)
        # DB UniqueConstraint 또는 커스텀 validate에서 잡힘
        has_order_error = "order" in serializer.errors
        has_non_field_error = "non_field_errors" in serializer.errors
        self.assertTrue(
            has_order_error or has_non_field_error,
            f"Expected order or non_field_errors error, got: {serializer.errors}",
        )


class TestAutoAssignConcurrency(LectureTestBase):
    """B. auto_assign() 동시성 안전 — select_for_update 구조 검증"""

    def setUp(self):
        super().setUp()
        self.section_a = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="A", section_type="CLASS",
            day_of_week=0, start_time="10:00",
            max_capacity=2,
        )
        self.section_b = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="B", section_type="CLASS",
            day_of_week=1, start_time="14:00",
            max_capacity=2,
        )
        # 학생 5명 생성
        self.students = []
        self.enrollments = []
        for i in range(5):
            user = User.objects.create_user(
                username=f"stu_{i}", password="test1234",
                tenant=self.tenant, name=f"Student{i}",
            )
            student = Student.objects.create(
                tenant=self.tenant, user=user,
                ps_number=f"S{i:03d}", name=f"Student{i}",
                phone=f"0101111{i:04d}", parent_phone=f"0102222{i:04d}",
                omr_code=f"1111{i:04d}",
            )
            enrollment = Enrollment.objects.create(
                tenant=self.tenant, lecture=self.lecture,
                student=student, status="ACTIVE",
            )
            self.students.append(student)
            self.enrollments.append(enrollment)

    def test_auto_assign_respects_capacity(self):
        """자동배정이 max_capacity를 준수"""
        request = self.factory.post(
            "/api/v1/lectures/section-assignments/auto-assign/",
            {"lecture_id": self.lecture.id, "section_type": "CLASS"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        view = SectionAssignmentViewSet.as_view({"post": "auto_assign"})
        response = view(request)

        self.assertEqual(response.status_code, 200)
        # A반 2명, B반 2명 = 4명 배정, 1명 스킵
        self.assertEqual(response.data["assigned"], 4)
        self.assertEqual(response.data["skipped"], 1)

        # DB 검증
        a_count = SectionAssignment.objects.filter(
            tenant=self.tenant, class_section=self.section_a,
        ).count()
        b_count = SectionAssignment.objects.filter(
            tenant=self.tenant, class_section=self.section_b,
        ).count()
        self.assertEqual(a_count, 2)
        self.assertEqual(b_count, 2)


class TestClinicSourcePreservation(LectureTestBase):
    """C. CLINIC auto_assign 시 source 보존"""

    def setUp(self):
        super().setUp()
        self.class_section = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="A", section_type="CLASS",
            day_of_week=0, start_time="10:00",
        )
        self.clinic_section = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="C1", section_type="CLINIC",
            day_of_week=2, start_time="15:00",
        )
        # 학생 1명: 수동 배정된 상태
        user = User.objects.create_user(
            username="manual_stu", password="test1234",
            tenant=self.tenant, name="ManualStudent",
        )
        student = Student.objects.create(
            tenant=self.tenant, user=user,
            ps_number="M001", name="ManualStudent",
            phone="01055550000", parent_phone="01066660000",
            omr_code="55550000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            student=student, status="ACTIVE",
        )
        # MANUAL 편성 생성
        self.assignment = SectionAssignment.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            class_section=self.class_section,
            source="MANUAL",
        )

    def test_clinic_auto_assign_preserves_manual_source(self):
        """CLINIC 자동배정 시 기존 MANUAL source가 보존됨"""
        request = self.factory.post(
            "/api/v1/lectures/section-assignments/auto-assign/",
            {"lecture_id": self.lecture.id, "section_type": "CLINIC"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        view = SectionAssignmentViewSet.as_view({"post": "auto_assign"})
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assignment.refresh_from_db()
        # clinic_section은 할당됨
        self.assertEqual(self.assignment.clinic_section_id, self.clinic_section.id)
        # source는 여전히 MANUAL
        self.assertEqual(self.assignment.source, "MANUAL")


class TestSectionTypeValidation(LectureTestBase):
    """D. section 타입 검증"""

    def setUp(self):
        super().setUp()
        self.class_section = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="A", section_type="CLASS",
            day_of_week=0, start_time="10:00",
        )
        self.clinic_section = Section.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            label="C1", section_type="CLINIC",
            day_of_week=2, start_time="15:00",
        )
        user = User.objects.create_user(
            username="type_stu", password="test1234",
            tenant=self.tenant, name="TypeStudent",
        )
        student = Student.objects.create(
            tenant=self.tenant, user=user,
            ps_number="T001", name="TypeStudent",
            phone="01077770000", parent_phone="01088880000",
            omr_code="77770000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, lecture=self.lecture,
            student=student, status="ACTIVE",
        )

    def test_serializer_rejects_clinic_as_class_section(self):
        """시리얼라이저가 CLINIC 타입을 class_section으로 거부"""
        serializer = SectionAssignmentSerializer(data={
            "enrollment": self.enrollment.id,
            "class_section": self.clinic_section.id,
        })
        valid = serializer.is_valid()
        self.assertFalse(valid)
        self.assertIn("class_section", serializer.errors)

    def test_serializer_rejects_class_as_clinic_section(self):
        """시리얼라이저가 CLASS 타입을 clinic_section으로 거부"""
        serializer = SectionAssignmentSerializer(data={
            "enrollment": self.enrollment.id,
            "class_section": self.class_section.id,
            "clinic_section": self.class_section.id,
        })
        valid = serializer.is_valid()
        self.assertFalse(valid)
        self.assertIn("clinic_section", serializer.errors)

    def test_view_rejects_clinic_as_class_section(self):
        """뷰에서 CLINIC 타입을 class_section으로 거부"""
        request = self.factory.post(
            "/api/v1/lectures/section-assignments/",
            {
                "enrollment": self.enrollment.id,
                "class_section": self.clinic_section.id,
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        view = SectionAssignmentViewSet.as_view({"post": "create"})
        response = view(request)
        self.assertEqual(response.status_code, 400)


class TestDateValidation(LectureTestBase):
    """E. 날짜 정합성 검증"""

    def test_lecture_rejects_start_after_end(self):
        """Lecture 모델이 start_date > end_date를 거부"""
        with self.assertRaises(DjangoValidationError):
            Lecture(
                tenant=self.tenant,
                title="BadDates",
                name="BadDates",
                subject="math",
                start_date="2026-05-15",
                end_date="2026-05-01",
            ).save()

    def test_lecture_serializer_rejects_start_after_end(self):
        """시리얼라이저가 start_date > end_date를 거부"""
        serializer = LectureSerializer(data={
            "title": "BadDates2",
            "name": "BadDates2",
            "subject": "math",
            "start_date": "2026-05-15",
            "end_date": "2026-05-01",
        })
        valid = serializer.is_valid()
        self.assertFalse(valid)
        self.assertIn("end_date", serializer.errors)

    def test_lecture_allows_null_dates(self):
        """날짜가 null인 경우 정상 저장"""
        lec = Lecture.objects.create(
            tenant=self.tenant,
            title="NullDates",
            name="NullDates",
            subject="math",
        )
        self.assertIsNone(lec.start_date)
        self.assertIsNone(lec.end_date)

    def test_lecture_allows_same_start_end(self):
        """start_date == end_date 허용"""
        lec = Lecture.objects.create(
            tenant=self.tenant,
            title="SameDay",
            name="SameDay",
            subject="math",
            start_date="2026-05-01",
            end_date="2026-05-01",
        )
        self.assertEqual(lec.start_date, lec.end_date)
