"""End-to-end contract tests for exam result Excel imports."""

from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result, ResultFact, ResultItem
from apps.domains.results.services.exam_result_excel_import import (
    apply_exam_result_import,
    build_exam_result_template,
    plan_exam_result_import,
)
from apps.domains.results.views.admin_exam_result_excel_import_view import (
    AdminExamResultExcelImportView,
    AdminExamResultExcelTemplateView,
)
from apps.domains.students.models import Student


User = get_user_model()


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


class ExamResultExcelImportTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Excel Results",
            code="excel-results",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="excel-results-admin",
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
            title="수학 A",
            name="수학 A",
            subject="MATH",
            color="#2563eb",
            chip_label="수A",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1차시",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="혼합형 시험",
            subject="수학",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )
        self.exam.sessions.add(self.session)
        self.sheet = Sheet.objects.create(
            exam=self.exam,
            name="MAIN",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        self.choice_question = ExamQuestion.objects.create(
            sheet=self.sheet,
            number=1,
            score=40,
        )
        self.short_question = ExamQuestion.objects.create(
            sheet=self.sheet,
            number=2,
            score=60,
        )
        self.enrollment = self._create_enrollment(
            name="김학생",
            username="excel-student",
            ps_number="EX-001",
            phone="01012345678",
            parent_phone="01098765432",
        )
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

    def _create_enrollment(
        self,
        *,
        name: str,
        username: str,
        ps_number: str,
        phone: str,
        parent_phone: str,
    ) -> Enrollment:
        user = User.objects.create_user(
            username=username,
            password="pw1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=name,
            ps_number=ps_number,
            omr_code=phone[-8:],
            phone=phone,
            parent_phone=parent_phone,
            school_type="HIGH",
            high_school="테스트고",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=student,
            status="ACTIVE",
        )

    def _request(self, method: str, path: str, *, data=None):
        request_method = getattr(self.factory, method)
        request = request_method(path, data=data or {}, format="multipart")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    def test_template_contains_roster_and_numbered_question_columns(self):
        payload = build_exam_result_template(exam=self.exam, tenant=self.tenant)

        workbook = load_workbook(io.BytesIO(payload), data_only=False)
        sheet = workbook["시험결과"]

        self.assertEqual(sheet.cell(8, 1).value, "수강등록ID")
        self.assertEqual(sheet.cell(8, 3).value, "이름")
        self.assertEqual(sheet.cell(8, 7).value, 1)
        self.assertEqual(sheet.cell(8, 8).value, 2)
        self.assertEqual(sheet.cell(9, 1).value, self.enrollment.id)
        self.assertEqual(sheet.cell(9, 3).value, "김학생")

    def test_template_escapes_formula_like_student_text(self):
        self.enrollment.student.name = "=HYPERLINK(\"https://invalid.example\")"
        self.enrollment.student.save(update_fields=["name", "updated_at"])

        payload = build_exam_result_template(exam=self.exam, tenant=self.tenant)
        workbook = load_workbook(io.BytesIO(payload), data_only=False)

        self.assertEqual(
            workbook["시험결과"].cell(9, 3).value,
            "'=HYPERLINK(\"https://invalid.example\")",
        )

    def test_existing_x_only_spreadsheet_is_matched_and_scored(self):
        payload = _workbook_bytes(
            [
                ["현장인원", "", "", ""],
                ["학교", "이름", "부모님연락처", "학생연락처", "출석", "결시", 1, 2, "점수"],
                ["테스트고", "김학생", "010-9876-5432", "010-1234-5678", "", "", "", "x", 40],
            ]
        )

        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="기존채점표.xlsx",
            workbook_bytes=payload,
        )

        self.assertTrue(plan.can_apply, plan.errors)
        self.assertEqual(len(plan.rows), 1)
        row = plan.rows[0]
        self.assertEqual(row.candidate.enrollment_id, self.enrollment.id)
        self.assertEqual(row.correct_count, 1)
        self.assertEqual(row.wrong_question_numbers, (2,))
        self.assertEqual(row.total_score, 40.0)
        self.assertEqual(row.max_score, 100.0)

    def test_existing_phone_only_spreadsheet_is_matched(self):
        payload = _workbook_bytes(
            [["학생연락처", 1, 2], ["010-1234-5678", "", "X"]]
        )

        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="연락처채점표.xlsx",
            workbook_bytes=payload,
        )

        self.assertTrue(plan.can_apply, plan.errors)
        self.assertEqual(plan.rows[0].candidate.enrollment_id, self.enrollment.id)
        self.assertEqual(plan.rows[0].wrong_question_numbers, (2,))

    def test_apply_persists_choice_and_short_answer_correctness(self):
        payload = _workbook_bytes(
            [
                ["이름", "학생전화번호", 1, 2],
                ["김학생", "01012345678", "O", "X"],
            ]
        )
        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="시험결과.xlsx",
            workbook_bytes=payload,
        )

        response = apply_exam_result_import(plan=plan)

        self.assertTrue(response["applied"])
        result = Result.objects.get(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
        )
        self.assertEqual(float(result.objective_score), 40.0)
        self.assertEqual(float(result.total_score), 40.0)
        self.assertEqual(float(result.max_score), 100.0)
        items = {
            item.question_id: item
            for item in ResultItem.objects.filter(result=result)
        }
        self.assertTrue(items[self.choice_question.id].is_correct)
        self.assertFalse(items[self.short_question.id].is_correct)
        self.assertEqual(items[self.choice_question.id].source, "excel_import")
        self.assertEqual(
            ResultFact.objects.filter(
                target_type="exam",
                target_id=self.exam.id,
                source="excel_import",
            ).count(),
            2,
        )

    def test_reimport_identical_values_does_not_duplicate_question_facts(self):
        payload = _workbook_bytes(
            [["수강등록ID", "이름", 1, 2], [self.enrollment.id, "김학생", "", "X"]]
        )
        first = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="same.xlsx",
            workbook_bytes=payload,
        )
        apply_exam_result_import(plan=first)
        second = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="same.xlsx",
            workbook_bytes=payload,
        )
        apply_exam_result_import(plan=second)

        self.assertEqual(
            ResultFact.objects.filter(
                target_type="exam",
                target_id=self.exam.id,
                source="excel_import",
            ).count(),
            2,
        )
        self.assertEqual(second.as_payload()["overwrite_count"], 1)

    def test_preview_rejects_unknown_marker_without_writes(self):
        payload = _workbook_bytes(
            [["이름", "학생연락처", 1, 2], ["김학생", "01012345678", "정답", "△"]]
        )

        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="invalid.xlsx",
            workbook_bytes=payload,
        )

        self.assertFalse(plan.can_apply)
        self.assertIn("2번", plan.errors[0]["message"])
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

    def test_preview_rejects_conflicting_name_and_phone(self):
        payload = _workbook_bytes(
            [["이름", "학생연락처", 1, 2], ["다른학생", "01012345678", "", "X"]]
        )

        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="conflicting-student.xlsx",
            workbook_bytes=payload,
        )

        self.assertFalse(plan.can_apply)
        self.assertIn("연락처와 학생 이름", plan.errors[0]["message"])
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

    def test_linked_session_roster_is_used_when_exam_assignment_is_empty(self):
        ExamEnrollment.objects.filter(exam=self.exam).delete()
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        payload = _workbook_bytes(
            [["이름", 1, 2], ["김학생", "", "X"]]
        )

        plan = plan_exam_result_import(
            exam=self.exam,
            tenant=self.tenant,
            filename="session-roster.xlsx",
            workbook_bytes=payload,
        )
        apply_exam_result_import(plan=plan)

        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=self.exam,
                enrollment=self.enrollment,
            ).exists()
        )

    def test_template_and_import_endpoints_use_the_same_contract(self):
        template_request = self._request(
            "get",
            f"/results/admin/exams/{self.exam.id}/result-import/template/",
        )
        template_response = AdminExamResultExcelTemplateView.as_view()(
            template_request,
            exam_id=self.exam.id,
        )
        self.assertEqual(template_response.status_code, 200)
        self.assertTrue(bytes(template_response.content).startswith(b"PK"))

        upload = SimpleUploadedFile(
            "results.xlsx",
            _workbook_bytes(
                [["수강등록ID", "이름", 1, 2], [self.enrollment.id, "김학생", "", "X"]]
            ),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        preview_request = self._request(
            "post",
            f"/results/admin/exams/{self.exam.id}/result-import/",
            data={"file": upload},
        )
        preview_response = AdminExamResultExcelImportView.as_view()(
            preview_request,
            exam_id=self.exam.id,
        )

        self.assertEqual(preview_response.status_code, 200, preview_response.data)
        self.assertTrue(preview_response.data["ok"])
        self.assertEqual(preview_response.data["matched_count"], 1)

    def test_other_tenant_exam_is_not_accessible(self):
        other_tenant = Tenant.objects.create(
            name="Other",
            code="excel-results-other",
            is_active=True,
        )
        other_exam = Exam.objects.create(
            tenant=other_tenant,
            title="Other exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        request = self._request(
            "get",
            f"/results/admin/exams/{other_exam.id}/result-import/template/",
        )

        response = AdminExamResultExcelTemplateView.as_view()(
            request,
            exam_id=other_exam.id,
        )

        self.assertEqual(response.status_code, 404)
