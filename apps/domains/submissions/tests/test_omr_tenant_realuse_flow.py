from __future__ import annotations

import cv2
import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

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
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result
from apps.domains.submissions.views.submission_view import SubmissionViewSet
from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7
from academy.adapters.ai.omr.identifier import IdentifierConfigV1, detect_identifier_v1
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from academy.adapters.ai.omr.warp import _try_contour_warp
from tests.omr.test_omr_full_pipeline import distort
from tests.omr.test_omr_realuse import render_marked_pdf


User = get_user_model()


class OMRTenantRealUseFlowTests(TestCase):
    def test_contour_fallback_rejects_narrow_internal_panel(self):
        image = np.ones((3507, 2480, 3), dtype=np.uint8) * 255
        cv2.rectangle(image, (80, 2697), (2380, 3441), (0, 0, 0), 3)

        result = _try_contour_warp(image, 3508, 2480)

        self.assertIsNone(result)

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

    def test_tenant_one_batch_omr_scans_map_grade_and_hold_unreadable_identifier(self):
        tag = "[E2E-OMR-BATCH]"

        tenant = Tenant.objects.create(
            name=f"{tag} Tenant",
            code="e2e_omr_batch_t1",
            is_active=True,
        )
        self.assertEqual(tenant.id, 1)

        staff = User.objects.create_user(
            username="e2e_omr_batch_staff",
            password="test1234",
            tenant=tenant,
            is_staff=True,
            name=f"{tag} Staff",
        )
        TenantMembership.ensure_active(tenant=tenant, user=staff, role="teacher")

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

        enrollments_by_code = {}
        for code, name in {
            "11150001": "perfect",
            "11110001": "all-wrong",
            "11139992": "unreadable-id",
        }.items():
            student_result = create_student_account(
                tenant=tenant,
                student_data={
                    "ps_number": f"E2EOMR{code}",
                    "name": f"{tag} {name}",
                    "phone": f"010{code}",
                    "parent_phone": "",
                    "omr_code": code,
                    "school_type": "HIGH",
                },
                password="test1234",
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
            ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
            enrollments_by_code[code] = enrollment

        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=20)
        questions = [
            ExamQuestion.objects.create(sheet=sheet, number=i, score=5)
            for i in range(1, 21)
        ]

        answer_key_by_number = {
            "1": ["1", "3"],
            "2": ["2", "3"],
            "3": ["3", "4"],
            **{str(i): "3" for i in range(4, 21)},
        }
        AnswerKey.objects.create(
            exam=exam,
            answers={str(q.id): answer_key_by_number[str(q.number)] for q in questions},
        )

        wrong_marks = {
            "1": ["1", "2"],
            "2": ["1", "4"],
            "3": ["1", "5"],
            **{str(i): "1" for i in range(4, 21)},
        }
        meta = build_omr_meta(question_count=20, n_choices=5)

        cases = [
            {
                "label": "perfect",
                "code": "11150001",
                "marks": answer_key_by_number,
                "id_digits": {i: int(d) for i, d in enumerate("11150001")},
                "expected_status": Submission.Status.ANSWERS_READY,
                "expected_score": 100,
            },
            {
                "label": "all-wrong",
                "code": "11110001",
                "marks": wrong_marks,
                "id_digits": {i: int(d) for i, d in enumerate("11110001")},
                "expected_status": Submission.Status.ANSWERS_READY,
                "expected_score": 0,
            },
            {
                "label": "unreadable-id",
                "code": "11139992",
                "marks": answer_key_by_number,
                "id_digits": {0: 1, 1: 1, 2: 1, 3: 3, 4: 9, 7: 2},
                "expected_status": Submission.Status.NEEDS_IDENTIFICATION,
                "expected_score": None,
            },
        ]

        for case in cases:
            image = render_marked_pdf(
                meta,
                case["marks"],
                case["id_digits"],
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
            self.assertEqual(len(answers), 20)
            if case["expected_status"] == Submission.Status.ANSWERS_READY:
                self.assertEqual(identifier["identifier"], case["code"])
            else:
                self.assertIn("?", identifier.get("raw_identifier", ""))

            submission = Submission.objects.create(
                tenant=tenant,
                user=staff,
                target_type=Submission.TargetType.EXAM,
                target_id=exam.id,
                source=Submission.Source.OMR_SCAN,
                status=Submission.Status.DISPATCHED,
                file_key=f"tenants/{tenant.id}/e2e/{case['label']}.jpg",
            )

            apply_omr_ai_result({
                "submission_id": submission.id,
                "tenant_id": tenant.id,
                "status": "DONE",
                "version": "v15",
                "aligned": True,
                "alignment_method": align.method,
                "identifier": identifier,
                "answers": [a.to_dict() for a in answers],
            })

            submission.refresh_from_db()
            self.assertEqual(submission.status, case["expected_status"])
            self.assertEqual(submission.answers.count(), 20)

            if case["expected_status"] == Submission.Status.NEEDS_IDENTIFICATION:
                self.assertIsNone(submission.enrollment_id)
                self.assertTrue(submission.meta["manual_review"]["required"])
                self.assertIn(
                    "IDENTIFIER_INCOMPLETE",
                    submission.meta["manual_review"]["reasons"],
                )
                self.assertEqual(submission.meta["identifier_status"], "incomplete")
                continue

            enrollment = enrollments_by_code[case["code"]]
            self.assertEqual(submission.enrollment_id, enrollment.id)
            self.assertFalse(submission.meta["manual_review"]["required"])
            self.assertEqual(submission.meta["answer_stats"]["total"], 20)

            exam_result = grade_submission(submission.id)
            submission.refresh_from_db()

            self.assertEqual(submission.status, Submission.Status.DONE)
            self.assertEqual(exam_result.total_score, case["expected_score"])
            self.assertEqual(exam_result.max_score, 100)
            self.assertTrue(
                Result.objects.filter(
                    target_type="exam",
                    target_id=exam.id,
                    enrollment_id=enrollment.id,
                    total_score=case["expected_score"],
                    max_score=100,
                ).exists()
            )


class OMRMapperReviewPolicyTests(TestCase):
    def _make_exam(
        self,
        *,
        answer_key: dict[str, object] | None = None,
        peer_phone: str | None = None,
        phone: str = "01012345678",
        parent_phone: str = "",
        omr_code: str = "12345678",
        create_exam_enrollment: bool = True,
        question_scores: tuple[float, float] = (50, 50),
        extra_answer_key_answers: dict[str, object] | None = None,
    ):
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
                "phone": phone,
                "parent_phone": parent_phone,
                "omr_code": omr_code,
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
        if create_exam_enrollment:
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
            ExamQuestion.objects.create(
                sheet=sheet,
                number=i,
                score=question_scores[i - 1],
            )
            for i in range(1, 3)
        ]
        answers = answer_key or {str(questions[0].id): "1", str(questions[1].id): "2"}
        if extra_answer_key_answers:
            answers = {**answers, **extra_answer_key_answers}
        AnswerKey.objects.create(
            exam=exam,
            answers=answers,
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

    def test_session_candidate_without_exam_enrollment_matches_parent_tail_and_creates_exam_enrollment(self):
        tenant, exam, enrollment, submission = self._make_exam(
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="55556666",
            create_exam_enrollment=False,
        )

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "33334444"},
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertEqual(submission.meta["identifier_status"], "matched")
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertTrue(
            ExamEnrollment.objects.filter(exam=exam, enrollment=enrollment).exists()
        )

    def test_session_candidate_without_exam_enrollment_matches_omr_code_and_creates_exam_enrollment(self):
        tenant, exam, enrollment, submission = self._make_exam(
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="82378990",
            create_exam_enrollment=False,
        )

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "82378990"},
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertEqual(submission.meta["identifier_status"], "matched")
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertTrue(
            ExamEnrollment.objects.filter(exam=exam, enrollment=enrollment).exists()
        )

    def test_zero_question_scores_fallback_to_exam_max_and_blank_extra_answer_keys_are_ignored(self):
        tenant, _exam, enrollment, submission = self._make_exam(
            question_scores=(0, 0),
            extra_answer_key_answers={"999999": ""},
        )

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "12345678"},
            "answers": [
                {"question_id": 1, "detected": ["1"], "status": "ok", "marking": "single", "confidence": 0.99},
                {"question_id": 2, "detected": ["2"], "status": "ok", "marking": "single", "confidence": 0.99},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)

        result = grade_submission(submission.id)

        self.assertEqual(result.total_score, 100)
        self.assertEqual(result.max_score, 100)

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

    def test_fully_blank_sheet_with_matched_identifier_grades_zero_without_manual_review(self):
        tenant, exam, enrollment, submission = self._make_exam()

        apply_omr_ai_result({
            "submission_id": submission.id,
            "tenant_id": tenant.id,
            "status": "DONE",
            "version": "v15",
            "aligned": True,
            "identifier": {"status": "ok", "identifier": "12345678"},
            "answers": [
                {"question_id": 1, "detected": [], "status": "blank", "marking": "blank", "confidence": 0.0},
                {"question_id": 2, "detected": [], "status": "blank", "marking": "blank", "confidence": 0.0},
            ],
        })

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, enrollment.id)
        self.assertFalse(submission.meta["manual_review"]["required"])
        self.assertEqual(submission.meta["answer_stats"]["blank"], 2)

        result = grade_submission(submission.id)
        submission.refresh_from_db()

        self.assertEqual(submission.status, Submission.Status.DONE)
        self.assertEqual(result.total_score, 0)
        self.assertEqual(result.max_score, 100)
        self.assertTrue(
            Result.objects.filter(
                target_type="exam",
                target_id=exam.id,
                enrollment_id=enrollment.id,
                total_score=0,
                max_score=100,
            ).exists()
        )

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


class AcceptFromDuplicatesTests(TestCase):
    """
    같은 (시험, 학생) 중복 OMR 후보를 한 번에 채택+나머지 폐기하는 endpoint 시나리오.

    - manual_edit GET 응답에 duplicate_siblings가 노출돼야 한다.
    - accept-from-duplicates POST 한 번으로 본 sub은 DONE까지 진행되고,
      다른 active 형제는 DONE이면 SUPERSEDED, 그 외는 'discarded:duplicate'로 폐기된다.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Dup", code="dup_tenant", is_active=True
        )
        self.staff = User.objects.create_user(
            username="dup_staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant, user=self.staff, role="teacher"
        )

        sr = create_student_account(
            tenant=self.tenant,
            student_data={
                "ps_number": "DUP001",
                "name": "중복 후보 학생",
                "phone": "01012345678",
                "parent_phone": "",
                "omr_code": "12345678",
                "school_type": "HIGH",
            },
            password="test1234",
        )
        self.student = sr.student

        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="L", name="L", subject="MATH"
        )
        self.session = Session.objects.create(
            lecture=self.lecture, order=1, title="S"
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )

        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="중복 시험",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        self.sheet = Sheet.objects.create(
            exam=self.exam, name="MAIN", total_questions=2
        )
        self.q1 = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=50)
        self.q2 = ExamQuestion.objects.create(sheet=self.sheet, number=2, score=50)
        AnswerKey.objects.create(
            exam=self.exam,
            answers={str(self.q1.id): "1", str(self.q2.id): "2"},
        )

        self.kept = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.NEEDS_IDENTIFICATION,
            file_key=f"tenants/{self.tenant.id}/omr/kept.jpg",
            meta={
                "manual_review": {"required": True, "reasons": ["DUPLICATE_ENROLLMENT"]},
                "identifier_status": "matched_duplicate",
            },
        )
        SubmissionAnswer.objects.create(
            submission=self.kept,
            tenant=self.tenant,
            exam_question_id=self.q1.id,
            answer="1",
        )
        SubmissionAnswer.objects.create(
            submission=self.kept,
            tenant=self.tenant,
            exam_question_id=self.q2.id,
            answer="3",  # 오답
        )

        self.other = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DONE,
            file_key=f"tenants/{self.tenant.id}/omr/other.jpg",
        )

    def _post_accept(self, sub_id: int, *, tenant=None, user=None):
        path = f"/submissions/submissions/{sub_id}/accept-from-duplicates/"
        request = self.factory.post(path)
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.staff)
        view = SubmissionViewSet.as_view({"post": "accept_from_duplicates"})
        return view(request, pk=sub_id)

    def _get_manual_edit(self, sub_id: int):
        path = f"/submissions/submissions/{sub_id}/manual-edit/"
        request = self.factory.get(path)
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        view = SubmissionViewSet.as_view({"get": "manual_edit"})
        return view(request, pk=sub_id)

    def test_manual_edit_get_exposes_duplicate_siblings(self):
        response = self._get_manual_edit(self.kept.id)
        self.assertEqual(response.status_code, 200, response.data)
        siblings = response.data.get("duplicate_siblings") or []
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["submission_id"], self.other.id)
        self.assertEqual(siblings[0]["status"], Submission.Status.DONE)

    def test_accept_from_duplicates_promotes_kept_and_supersedes_other(self):
        response = self._post_accept(self.kept.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.kept.refresh_from_db()
        self.other.refresh_from_db()

        self.assertEqual(self.kept.status, Submission.Status.DONE)
        self.assertFalse(
            (self.kept.meta or {}).get("manual_review", {}).get("required")
        )
        self.assertEqual(
            (self.kept.meta or {}).get("identifier_status"), "matched"
        )
        self.assertEqual(
            (self.kept.meta or {}).get("accepted_from_duplicates", {}).get(
                "superseded_sibling_count"
            ),
            1,
        )

        # DONE 형제는 SUPERSEDED (재시험과 동일 도메인 의미)
        self.assertEqual(self.other.status, Submission.Status.SUPERSEDED)
        self.assertEqual(
            (self.other.meta or {}).get("discarded", {}).get("kept_sibling_id"),
            self.kept.id,
        )
        self.assertEqual(
            (self.other.meta or {}).get("discarded", {}).get("reason"),
            "superseded_by_duplicate_selection",
        )

        self.assertEqual(response.data["superseded_count"], 1)
        self.assertEqual(response.data["discarded_count"], 0)
        self.assertEqual(response.data["status"], Submission.Status.DONE)
        self.assertEqual(response.data["score"], 50.0)
        self.assertEqual(response.data["max_score"], 100.0)

        result = Result.objects.filter(
            target_type="exam",
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
        ).order_by("-id").first()
        self.assertIsNotNone(result)
        self.assertEqual(float(result.total_score or 0), 50.0)

    def test_rejects_when_enrollment_missing(self):
        unmatched = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=None,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.NEEDS_IDENTIFICATION,
            file_key="x.jpg",
        )
        response = self._post_accept(unmatched.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("학생 식별", response.data.get("detail", ""))

    def test_tenant_isolation(self):
        other_tenant = Tenant.objects.create(
            name="Other", code="other_dup", is_active=True
        )
        other_staff = User.objects.create_user(
            username="other_dup_staff",
            password="test1234",
            tenant=other_tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=other_tenant, user=other_staff, role="teacher"
        )
        response = self._post_accept(
            self.kept.id, tenant=other_tenant, user=other_staff
        )
        self.assertIn(response.status_code, (403, 404))


class IdentifierMatcherTests(TestCase):
    """
    IdentifierMatcher silent 1-digit error 방어 시나리오.

    워커가 status='ok' 로 보고하더라도 digits 안에 status='ambiguous' 자리가
    하나라도 있으면 시험 내 다른 학생이 1 자리 변형으로 매칭되는지 검증한다.
    모든 자리 ok 면 워커를 신뢰한다 (false positive 0).
    """

    def _make_exam_with_two_close_students(self):
        tenant = Tenant.objects.create(
            name="IDM", code="idm_tenant", is_active=True
        )
        staff = User.objects.create_user(
            username="idm_staff", password="x", tenant=tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=staff, role="teacher")
        lecture = Lecture.objects.create(
            tenant=tenant, title="L", name="L", subject="MATH"
        )
        session = Session.objects.create(lecture=lecture, order=1, title="S")
        exam = Exam.objects.create(
            tenant=tenant,
            title="EXAM",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)
        students = {}
        for code, name in (("12345678", "A"), ("12345679", "B")):
            sr = create_student_account(
                tenant=tenant,
                student_data={
                    "ps_number": f"IDM-{code}",
                    "name": name,
                    "phone": f"010{code}",
                    "parent_phone": "",
                    "omr_code": code,
                    "school_type": "HIGH",
                },
                password="x",
            )
            enr = Enrollment.objects.create(
                tenant=tenant,
                student=sr.student,
                lecture=lecture,
                status="ACTIVE",
            )
            SessionEnrollment.objects.create(
                tenant=tenant, session=session, enrollment=enr
            )
            ExamEnrollment.objects.create(exam=exam, enrollment=enr)
            students[code] = enr
        return tenant, exam, students

    def test_status_ok_without_ambiguous_digits_trusts_worker(self):
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatcher,
        )

        tenant, exam, students = self._make_exam_with_two_close_students()
        matcher = IdentifierMatcher(tenant=tenant, exam_id=exam.id)
        result = matcher.match({
            "status": "ok",
            "identifier": "12345678",
            "digits": [
                {"digit_index": i, "status": "ok", "value": int("12345678"[i])}
                for i in range(8)
            ],
        })
        self.assertEqual(result.enrollment_id, students["12345678"].id)
        self.assertEqual(result.kind, "exact")
        self.assertFalse(result.needs_review)
        self.assertEqual(result.identifier_status, "matched")

    def test_status_ok_with_one_ambiguous_digit_catches_competitor(self):
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatcher,
        )

        tenant, exam, students = self._make_exam_with_two_close_students()
        matcher = IdentifierMatcher(tenant=tenant, exam_id=exam.id)
        digits = [
            {"digit_index": i, "status": "ok", "value": int("12345678"[i])}
            for i in range(7)
        ]
        digits.append({
            "digit_index": 7,
            "status": "ambiguous",
            "value": 8,
            "marks": [{"number": 8}, {"number": 9}],
        })
        result = matcher.match({
            "status": "ok",
            "identifier": "12345678",
            "digits": digits,
        })
        self.assertEqual(result.enrollment_id, students["12345678"].id)
        self.assertEqual(result.kind, "exact_with_competitor")
        self.assertTrue(result.needs_review)
        self.assertIn("IDENTIFIER_AMBIGUOUS_DIGIT", result.review_reasons)
        self.assertEqual(result.identifier_status, "matched_ambiguous")

    def test_status_ambiguous_without_competitor_resolves(self):
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatcher,
        )

        tenant = Tenant.objects.create(name="IDM2", code="idm2", is_active=True)
        staff = User.objects.create_user(
            username="idm2_staff", password="x", tenant=tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=staff, role="teacher")
        lecture = Lecture.objects.create(
            tenant=tenant, title="L", name="L", subject="MATH"
        )
        session = Session.objects.create(lecture=lecture, order=1, title="S")
        exam = Exam.objects.create(
            tenant=tenant,
            title="E",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        exam.sessions.add(session)
        sr = create_student_account(
            tenant=tenant,
            student_data={
                "ps_number": "IDM2-1",
                "name": "Only",
                "phone": "01099887766",
                "parent_phone": "",
                "omr_code": "99887766",
                "school_type": "HIGH",
            },
            password="x",
        )
        enr = Enrollment.objects.create(
            tenant=tenant, student=sr.student, lecture=lecture, status="ACTIVE"
        )
        SessionEnrollment.objects.create(
            tenant=tenant, session=session, enrollment=enr
        )
        ExamEnrollment.objects.create(exam=exam, enrollment=enr)
        matcher = IdentifierMatcher(tenant=tenant, exam_id=exam.id)
        digits = [
            {"digit_index": i, "status": "ok", "value": int("99887766"[i])}
            for i in range(7)
        ]
        digits.append({
            "digit_index": 7,
            "status": "ambiguous",
            "value": 6,
            "marks": [{"number": 6}, {"number": 8}, {"number": 0}],
        })
        result = matcher.match({
            "status": "ambiguous",
            "identifier": "99887766",
            "digits": digits,
        })
        self.assertEqual(result.enrollment_id, enr.id)
        self.assertEqual(result.kind, "exact")
        self.assertFalse(result.needs_review)
        self.assertEqual(result.identifier_status, "matched_ambiguous_resolved")

    def test_incomplete_identifier_returns_incomplete(self):
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatcher,
        )

        tenant = Tenant.objects.create(name="IDM3", code="idm3", is_active=True)
        exam = Exam.objects.create(
            tenant=tenant,
            title="E",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        matcher = IdentifierMatcher(tenant=tenant, exam_id=exam.id)
        result = matcher.match({
            "status": "ok",
            "identifier": "1234567?",
        })
        self.assertIsNone(result.enrollment_id)
        self.assertEqual(result.kind, "incomplete")
        self.assertEqual(result.identifier_status, "incomplete")
        self.assertIn("IDENTIFIER_INCOMPLETE", result.review_reasons)

    def test_no_identifier_returns_missing(self):
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatcher,
            IdentifierMatchResult,
        )

        tenant = Tenant.objects.create(name="IDM4", code="idm4", is_active=True)
        exam = Exam.objects.create(
            tenant=tenant,
            title="E",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        matcher = IdentifierMatcher(tenant=tenant, exam_id=exam.id)
        result = matcher.match(None)
        self.assertIsInstance(result, IdentifierMatchResult)
        self.assertIsNone(result.enrollment_id)
        self.assertEqual(result.kind, "missing")
