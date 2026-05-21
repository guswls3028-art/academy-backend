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
from django.db import IntegrityError, connection
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.program import Program
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session, Section, SectionAssignment
from apps.domains.lectures.serializers import (
    LectureSerializer,
    SessionSerializer,
    SectionAssignmentSerializer,
)
from apps.domains.lectures.views import SectionAssignmentViewSet
from apps.domains.lectures.views import LectureViewSet
from apps.domains.lectures.views import SessionViewSet
from apps.domains.lectures.views import SectionViewSet
from apps.domains.enrollment.models import Enrollment
from apps.domains.video.models import Video, VideoProgress

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
        Program.objects.update_or_create(
            tenant=self.tenant,
            defaults={
                "display_name": "TestAcademy",
                "brand_key": "test_lec",
                "feature_flags": {
                    "section_mode": True,
                    "clinic_mode": "regular",
                },
            },
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


class TestSessionListNoPagination(LectureTestBase):
    """차시 목록은 성적/시험/영상 트리 진입점에서 전체가 필요하다."""

    def test_session_list_returns_all_rows_over_global_page_size(self):
        """전역 PAGE_SIZE=20을 넘어도 같은 강의의 모든 차시를 반환."""
        for i in range(25):
            Session.objects.create(
                lecture=self.lecture,
                order=i + 1,
                title=f"{i + 1}차시",
            )

        request = self.factory.get(
            f"/api/v1/lectures/sessions/?lecture={self.lecture.id}"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 25)
        self.assertEqual(response.data[0]["order"], 1)
        self.assertEqual(response.data[-1]["order"], 25)

    def test_include_progress_annotates_total_without_per_session_counts(self):
        """선생앱 오늘 카드의 출결 총원은 세션 수와 무관하게 한 쿼리에서 계산된다."""
        students = []
        enrollments = []
        for idx in range(3):
            user = User.objects.create_user(
                username=f"progress_stu_{idx}",
                password="test1234",
                tenant=self.tenant,
                name=f"Progress Student {idx}",
            )
            student = Student.objects.create(
                tenant=self.tenant,
                user=user,
                ps_number=f"P{idx:03d}",
                name=f"Progress Student {idx}",
                phone=f"0103311{idx:04d}",
                parent_phone=f"0104422{idx:04d}",
                omr_code=f"3311{idx:04d}",
            )
            students.append(student)
            enrollments.append(
                Enrollment.objects.create(
                    tenant=self.tenant,
                    student=student,
                    lecture=self.lecture,
                    status="ACTIVE",
                )
            )

        section = Section.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            label="A",
            section_type="CLASS",
            day_of_week=0,
            start_time="10:00",
        )
        SectionAssignment.objects.create(
            tenant=self.tenant,
            enrollment=enrollments[0],
            class_section=section,
        )
        SectionAssignment.objects.create(
            tenant=self.tenant,
            enrollment=enrollments[1],
            class_section=section,
        )

        shared_session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="공통 1차시",
        )
        section_session = Session.objects.create(
            lecture=self.lecture,
            section=section,
            order=1,
            title="A반 1차시",
        )
        for idx in range(2, 9):
            Session.objects.create(
                lecture=self.lecture,
                order=idx,
                title=f"공통 {idx}차시",
            )

        request = self.factory.get("/api/v1/lectures/sessions/?include_progress=1")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        with CaptureQueriesContext(connection) as captured:
            response = SessionViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        totals = {row["id"]: row["attendance_total"] for row in response.data}
        self.assertEqual(totals[shared_session.id], 3)
        self.assertEqual(totals[section_session.id], 2)
        self.assertLessEqual(
            len(captured),
            5,
            [query["sql"] for query in captured.captured_queries],
        )


class TestSectionBulkCreateSessions(LectureTestBase):
    """반별 차시 일괄 생성은 한 번의 요청에서 같은 차시 순번을 공유한다."""

    def setUp(self):
        super().setUp()
        self.section_a = Section.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            label="A",
            section_type="CLASS",
            day_of_week=0,
            start_time="10:00",
        )
        self.section_b = Section.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            label="B",
            section_type="CLASS",
            day_of_week=1,
            start_time="11:00",
        )

    def _bulk_create(self, dates):
        request = self.factory.post(
            "/api/v1/lectures/sections/bulk-create-sessions/",
            {"lecture_id": self.lecture.id, "title": "", "dates": dates},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return SectionViewSet.as_view({"post": "bulk_create_sessions"})(request)

    def test_bulk_create_uses_one_shared_next_order_for_all_sections(self):
        Session.objects.create(
            lecture=self.lecture,
            section=self.section_a,
            order=1,
            title="A 1차시",
        )
        Session.objects.create(
            lecture=self.lecture,
            section=self.section_a,
            order=2,
            title="A 2차시",
        )

        response = self._bulk_create({"A": "2026-04-15", "B": "2026-04-16"})

        self.assertEqual(response.status_code, 201)
        created_by_section = {row["section_label"]: row for row in response.data}
        self.assertEqual(created_by_section["A"]["order"], 3)
        self.assertEqual(created_by_section["B"]["order"], 3)
        self.assertEqual(created_by_section["A"]["title"], "3차시")
        self.assertEqual(created_by_section["B"]["title"], "3차시")

    def test_bulk_create_rejects_unknown_section_label(self):
        response = self._bulk_create({"A": "2026-04-15", "Z": "2026-04-16"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Session.objects.count(), 0)


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


class TestLectureReportProgress(LectureTestBase):
    """F. 강의 리포트 영상 진척률 계산"""

    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1차시",
        )
        self.video1 = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="V1",
            file_key="",
            order=1,
            status=Video.Status.READY,
        )
        self.video2 = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="V2",
            file_key="",
            order=2,
            status=Video.Status.READY,
        )
        self.enrollments = []
        for i in range(2):
            user = User.objects.create_user(
                username=f"report_stu_{i}",
                password="test1234",
                tenant=self.tenant,
                name=f"ReportStudent{i}",
            )
            student = Student.objects.create(
                tenant=self.tenant,
                user=user,
                ps_number=f"R{i:03d}",
                name=f"ReportStudent{i}",
                phone=f"0103333{i:04d}",
                parent_phone=f"0104444{i:04d}",
                omr_code=f"3333{i:04d}",
            )
            enrollment = Enrollment.objects.create(
                tenant=self.tenant,
                lecture=self.lecture,
                student=student,
                status="ACTIVE",
            )
            self.enrollments.append(enrollment)

    def test_report_uses_real_video_progress(self):
        """placeholder 0.0 대신 VideoProgress 기반 평균/완료 수를 반환"""
        first, second = self.enrollments
        VideoProgress.objects.create(
            video=self.video1,
            enrollment=first,
            progress=1.0,
            completed=True,
        )
        VideoProgress.objects.create(
            video=self.video2,
            enrollment=first,
            progress=0.95,
            completed=True,
        )
        VideoProgress.objects.create(
            video=self.video1,
            enrollment=second,
            progress=0.25,
            completed=False,
        )

        request = self.factory.get(
            f"/api/v1/lectures/lectures/{self.lecture.id}/report/"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = LectureViewSet.as_view({"get": "report"})(
            request,
            pk=self.lecture.id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["total_videos"], 2)
        self.assertEqual(response.data["summary"]["avg_video_progress"], 55.0)
        self.assertEqual(response.data["summary"]["completed_students"], 1)

        rows = {
            row["enrollment"]: row
            for row in response.data["students"]
        }
        self.assertEqual(rows[first.id]["avg_progress"], 97.5)
        self.assertEqual(rows[first.id]["completed_videos"], 2)
        self.assertEqual(rows[second.id]["avg_progress"], 12.5)
        self.assertEqual(rows[second.id]["completed_videos"], 0)
