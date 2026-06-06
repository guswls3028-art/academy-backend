from __future__ import annotations

from django.test import TestCase

from apps.core.models.tenant import Tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.identity import derive_student_omr_code
from apps.support.omr.candidate_matching import (
    ensure_exam_enrollment_candidate,
    exact_enrollment_ids_by_identifier,
    resolve_enrollment_by_identifier,
)


class OmrCandidateMatchingTests(TestCase):
    def _create_exam_target(
        self,
        *,
        phone: str = "01011112222",
        parent_phone: str = "01087654321",
        omr_code: str = "24681357",
    ):
        tenant = Tenant.objects.create(
            name="[OMR] Tenant",
            code="omr_candidate_tenant",
            is_active=True,
        )
        student_result = create_student_account(
            tenant=tenant,
            student_data={
                "ps_number": "OMR-CAND-001",
                "name": "[OMR] Student",
                "phone": phone,
                "parent_phone": parent_phone,
                "omr_code": omr_code,
                "school_type": "HIGH",
            },
            password="test1234",
        )
        lecture = Lecture.objects.create(
            tenant=tenant,
            title="[OMR] Lecture",
            name="[OMR] Lecture",
            subject="MATH",
        )
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="[OMR] Session",
        )
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student_result.student,
            lecture=lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=tenant,
            session=session,
            enrollment=enrollment,
        )
        exam = Exam.objects.create(
            tenant=tenant,
            title="[OMR] Exam",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)
        return tenant, exam, enrollment

    def test_session_enrollment_candidate_resolves_parent_tail(self):
        tenant, exam, enrollment = self._create_exam_target()

        enrollment_id, kind = resolve_enrollment_by_identifier(
            tenant=tenant,
            exam_id=exam.id,
            identifier="87654321",
        )

        self.assertEqual(enrollment_id, enrollment.id)
        self.assertEqual(kind, "exact")

    def test_session_enrollment_candidate_can_be_materialized_as_exam_enrollment(self):
        tenant, exam, enrollment = self._create_exam_target()

        self.assertFalse(
            ExamEnrollment.objects.filter(
                exam=exam,
                enrollment=enrollment,
            ).exists()
        )

        ok = ensure_exam_enrollment_candidate(
            tenant=tenant,
            exam_id=exam.id,
            enrollment_id=enrollment.id,
        )

        self.assertTrue(ok)
        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=exam,
                enrollment=enrollment,
            ).exists()
        )

    def test_omr_code_is_an_exact_identifier_source(self):
        tenant, exam, enrollment = self._create_exam_target(omr_code="13572468")

        enrollment_id, kind = resolve_enrollment_by_identifier(
            tenant=tenant,
            exam_id=exam.id,
            identifier="13572468",
        )
        exact_ids = exact_enrollment_ids_by_identifier(
            tenant=tenant,
            exam_id=exam.id,
            identifier="13572468",
        )

        self.assertEqual(enrollment_id, enrollment.id)
        self.assertEqual(kind, "exact")
        self.assertEqual(exact_ids, {enrollment.id})

    def test_same_parent_tail8_candidates_require_manual_matching(self):
        tenant = Tenant.objects.create(
            name="[OMR] Shared Parent Tenant",
            code="omr_shared_parent_tenant",
            is_active=True,
        )
        parent_phone = "01044443333"
        lecture = Lecture.objects.create(
            tenant=tenant,
            title="[OMR] Shared Parent Lecture",
            name="[OMR] Shared Parent Lecture",
            subject="MATH",
        )
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="[OMR] Shared Parent Session",
        )
        exam = Exam.objects.create(
            tenant=tenant,
            title="[OMR] Shared Parent Exam",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)

        enrollments = []
        for idx, name in enumerate(["첫째학생", "둘째학생"], start=1):
            created = create_student_account(
                tenant=tenant,
                student_data={
                    "ps_number": f"OMR-SHARED-{idx}",
                    "name": name,
                    "phone": None,
                    "parent_phone": parent_phone,
                    "omr_code": derive_student_omr_code(phone=None, parent_phone=parent_phone),
                    "uses_identifier": True,
                    "school_type": "HIGH",
                },
                password="test1234",
            )
            enrollment = Enrollment.objects.create(
                tenant=tenant,
                student=created.student,
                lecture=lecture,
                status="ACTIVE",
            )
            SessionEnrollment.objects.create(
                tenant=tenant,
                session=session,
                enrollment=enrollment,
            )
            enrollments.append(enrollment)

        enrollment_id, kind = resolve_enrollment_by_identifier(
            tenant=tenant,
            exam_id=exam.id,
            identifier="44443333",
        )
        exact_ids = exact_enrollment_ids_by_identifier(
            tenant=tenant,
            exam_id=exam.id,
            identifier="44443333",
        )

        self.assertIsNone(enrollment_id)
        self.assertEqual(kind, "none")
        self.assertEqual(exact_ids, {enrollments[0].id, enrollments[1].id})
