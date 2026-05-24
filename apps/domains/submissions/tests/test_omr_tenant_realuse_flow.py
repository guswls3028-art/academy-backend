from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import TenantMembership
from apps.core.models.tenant import Tenant
from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamEnrollment,
    ExamQuestion,
    Sheet,
)
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result
from apps.domains.results.services.answer_matching import answer_matches
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.students.services.creation import create_student_account
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result
from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7
from academy.adapters.ai.omr.identifier import IdentifierConfigV1, detect_identifier_v1
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from tests.omr.test_omr_full_pipeline import distort
from tests.omr.test_omr_realuse import render_marked_pdf


User = get_user_model()


class OMRTenantRealUseFlowTests(TestCase):
    def test_tenant_one_omr_scan_maps_student_and_grades(self):
        tag = "[E2E-OMR-REALUSE]"

        tenant = Tenant.objects.create(
            name=f"{tag} Tenant",
            code="e2e_omr_realuse_t1",
            is_active=True,
        )
        self.assertEqual(tenant.id, 1)

        staff = User.objects.create_user(
            username="e2e_omr_staff",
            password="test1234",
            tenant=tenant,
            is_staff=True,
            name=f"{tag} Staff",
        )
        TenantMembership.ensure_active(tenant=tenant, user=staff, role="teacher")

        student_result = create_student_account(
            tenant=tenant,
            student_data={
                "ps_number": "E2EOMR001",
                "name": f"{tag} Student",
                "phone": "01012345678",
                "parent_phone": "01087654321",
                "omr_code": "12345678",
                "school_type": "HIGH",
            },
            password="test1234",
        )
        student = student_result.student

        lecture = Lecture.objects.create(
            tenant=tenant,
            title=f"{tag} Lecture",
            name=f"{tag} Lecture",
            subject="MATH",
        )
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title=f"{tag} Session",
        )
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student,
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
            title=f"{tag} Exam",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)

        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=20)
        questions = [
            ExamQuestion.objects.create(sheet=sheet, number=i, score=5)
            for i in range(1, 21)
        ]

        meta = build_omr_meta(question_count=20, n_choices=5)
        marks = {str(i): str(((i - 1) % 5) + 1) for i in range(1, 21)}
        marks["1"] = ["1", "3"]
        id_digits = {i: int(d) for i, d in enumerate("12345678")}

        image = render_marked_pdf(
            meta,
            marks,
            id_digits,
            dpi=200,
            jpeg_quality=70,
        )
        scanned = distort(image, dpi=200, rotation_deg=1.0, noise_sigma=5.0)
        align = align_to_a4_landscape(image_bgr=scanned, meta=meta)
        answers = detect_omr_answers_v7(
            image_bgr=align.image,
            meta=meta,
            config=AnswerDetectConfig(),
        )
        identifier = detect_identifier_v1(
            image_bgr=align.image,
            meta=meta,
            cfg=IdentifierConfigV1(),
        )

        self.assertEqual(align.method, "marker_homography")
        self.assertEqual(identifier["status"], "ok")
        self.assertEqual(identifier["identifier"], "12345678")
        q1_answer = next(a for a in answers if a.question_id == 1)
        self.assertEqual(set(q1_answer.detected), {"1", "3"})
        self.assertEqual(q1_answer.marking, "multi")
        self.assertEqual(q1_answer.status, "ok")
        self.assertTrue(all(a.status == "ok" for a in answers if a.question_id != 1))
        for a in answers:
            self.assertTrue(answer_matches(a.detected, marks[str(a.question_id)]))

        answer_key_payload = {str(q.id): marks[str(q.number)] for q in questions}
        answer_key_payload[str(questions[1].id)] = "1|2"
        AnswerKey.objects.create(exam=exam, answers=answer_key_payload)

        submission = Submission.objects.create(
            tenant=tenant,
            user=staff,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DISPATCHED,
            file_key=f"tenants/{tenant.id}/e2e/omr-realuse.jpg",
        )

        answer_payload = [a.to_dict() for a in answers]
        for item in answer_payload:
            if int(item["question_id"]) == 3:
                item["confidence"] = 0.2
                break

        apply_omr_ai_result(
            {
                "submission_id": submission.id,
                "tenant_id": tenant.id,
                "status": "DONE",
                "version": "v15",
                "aligned": True,
                "alignment_method": align.method,
                "identifier": identifier,
                "answers": answer_payload,
            }
        )

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertEqual(submission.meta["answer_stats"]["ok"], 20)
        self.assertEqual(submission.meta["answer_stats"]["ambiguous"], 0)

        exam_result = grade_submission(submission.id)
        submission.refresh_from_db()

        self.assertEqual(submission.status, Submission.Status.DONE)
        self.assertEqual(exam_result.total_score, 100)
        self.assertEqual(exam_result.max_score, 100)
        self.assertTrue(exam_result.breakdown["1"]["correct"])
        self.assertEqual(exam_result.breakdown["1"]["correct_answer"], "1,3")
        self.assertTrue(exam_result.breakdown["2"]["correct"])
        self.assertTrue(
            Result.objects.filter(
                target_type="exam",
                target_id=exam.id,
                enrollment_id=enrollment.id,
                total_score=100,
                max_score=100,
            ).exists()
        )


class OMRMapperReviewPolicyTests(TestCase):
    def _make_exam(self, *, answer_key: dict[str, object] | None = None, peer_phone: str | None = None):
        tenant = Tenant.objects.create(name="OMR Policy", code="omr_policy", is_active=True)
        staff = User.objects.create_user(
            username="omr_policy_staff",
            password="test1234",
            tenant=tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=staff, role="teacher")

        student_result = create_student_account(
            tenant=tenant,
            student_data={
                "ps_number": "OMRPOL001",
                "name": "OMR 정책 학생",
                "phone": "01012345678",
                "parent_phone": "",
                "omr_code": "12345678",
                "school_type": "HIGH",
            },
            password="test1234",
        )

        lecture = Lecture.objects.create(
            tenant=tenant,
            title="OMR 정책 강의",
            name="OMR 정책 강의",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1차시")
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student_result.student,
            lecture=lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(tenant=tenant, session=session, enrollment=enrollment)

        exam = Exam.objects.create(
            tenant=tenant,
            title="OMR 정책 시험",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)

        if peer_phone:
            peer_result = create_student_account(
                tenant=tenant,
                student_data={
                    "ps_number": "OMRPOL002",
                    "name": "OMR 경쟁 학생",
                    "phone": peer_phone,
                    "parent_phone": "",
                    "omr_code": peer_phone[-8:],
                    "school_type": "HIGH",
                },
                password="test1234",
            )
            peer_enrollment = Enrollment.objects.create(
                tenant=tenant,
                student=peer_result.student,
                lecture=lecture,
                status="ACTIVE",
            )
            SessionEnrollment.objects.create(tenant=tenant, session=session, enrollment=peer_enrollment)
            ExamEnrollment.objects.create(exam=exam, enrollment=peer_enrollment)

        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=2)
        questions = [
            ExamQuestion.objects.create(sheet=sheet, number=i, score=50)
            for i in range(1, 3)
        ]
        AnswerKey.objects.create(
            exam=exam,
            answers=answer_key or {str(questions[0].id): "1", str(questions[1].id): "2"},
        )
        submission = Submission.objects.create(
            tenant=tenant,
            user=staff,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DISPATCHED,
            file_key=f"tenants/{tenant.id}/omr/policy.jpg",
        )
        return tenant, exam, enrollment, submission

    def test_sparse_blank_answer_is_auto_zero_not_manual_review(self):
        tenant, _exam, enrollment, submission = self._make_exam()

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "12345678"},
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": [], "status": "blank", "marking": "blank", "confidence": 0.0},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertEqual(submission.meta["answer_stats"]["blank"], 1)

    def test_ambiguous_multi_with_no_correct_overlap_is_auto_wrong(self):
        tenant, _exam, enrollment, submission = self._make_exam()

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "12345678"},
            "answers": [
                {"question_id": 1, "detected": ["3", "4"], "status": "ambiguous", "marking": "multi", "confidence": 0.02},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertEqual(submission.meta["answer_stats"]["ambiguous"], 1)

    def test_clear_multi_answer_is_scored_without_manual_review(self):
        tenant, _exam, enrollment, submission = self._make_exam()

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "12345678"},
            "answers": [
                {
                    "question_id": 1,
                    "detected": ["1", "2"],
                    "status": "ok",
                    "marking": "multi",
                    "confidence": 0.8,
                },
                {
                    "question_id": 2,
                    "detected": ["2"],
                    "status": "ok",
                    "marking": "single",
                    "confidence": 0.99,
                },
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertEqual(submission.meta["answer_stats"]["ok"], 2)

    def test_ambiguous_identifier_without_competing_candidate_is_resolved(self):
        tenant, _exam, enrollment, submission = self._make_exam()

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {
                "status": "ambiguous",
                "identifier": "12345678",
                "digits": [
                    {
                        "digit_index": 0,
                        "value": 1,
                        "status": "ambiguous",
                        "marks": [{"number": 1}, {"number": 9}],
                    }
                ],
            },
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertEqual(submission.meta["identifier_status"], "matched_ambiguous_resolved")
        self.assertFalse(submission.meta["manual_review"]["required"])

    def test_ambiguous_identifier_with_competing_candidate_stays_manual(self):
        tenant, _exam, enrollment, submission = self._make_exam(peer_phone="01092345678")

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {
                "status": "ambiguous",
                "identifier": "12345678",
                "digits": [
                    {
                        "digit_index": 0,
                        "value": 1,
                        "status": "ambiguous",
                        "marks": [{"number": 1}, {"number": 9}],
                    }
                ],
            },
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertEqual(submission.meta["identifier_status"], "matched_ambiguous")
        self.assertTrue(submission.meta["manual_review"]["required"])
        self.assertIn("IDENTIFIER_AMBIGUOUS_DIGIT", submission.meta["manual_review"]["reasons"])
