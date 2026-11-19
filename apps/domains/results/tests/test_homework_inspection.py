from io import BytesIO
from unittest.mock import Mock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import HomeworkInspection
from apps.domains.results.services.homework_inspection_excel import HEADERS, build_template, read_preview
from apps.domains.results.views.homework_inspection_view import HomeworkInspectionView
from apps.support.student_app.results_summary import build_student_grades_summary


def stream(book):
    result = BytesIO()
    book.save(result)
    result.seek(0)
    return result


class HomeworkInspectionWorkbookTests(SimpleTestCase):
    scope = (1, 2, 3)
    item = {"enrollment_id": 10, "student_name": "수강생", "solved": 2, "total": 3, "feedback": "기존", "version": 2}

    def test_template_adds_literal_names_without_rescanning_the_roster(self):
        items = [{**self.item, "enrollment_id": i, "student_name": f"=학생{i}"} for i in range(1, 31)]
        scans = Mock(wraps=Worksheet.max_row.fget)
        with patch.object(Worksheet, "max_row", property(scans)):
            build_template(self.scope, "과제", items[:1], 15)
            small_roster_scans = scans.call_count
            scans.reset_mock()
            blob = build_template(self.scope, "과제", items, 15)
            self.assertEqual(scans.call_count, small_roster_scans)
        book = load_workbook(BytesIO(blob))
        try:
            for row_number, item in enumerate(items, start=8):
                name = book.active.cell(row_number, 2)
                self.assertEqual((name.value, name.data_type), (item["student_name"], "s"))
                self.assertEqual(book.active.cell(row_number, 6).value, item["enrollment_id"])
        finally:
            book.close()

    def test_template_roundtrip_and_blank_preservation(self):
        blob = build_template(self.scope, "과제", [self.item], 12)
        self.assertEqual(read_preview(BytesIO(blob), self.scope, [self.item]), {"rows": [], "errors": []})
        book = load_workbook(BytesIO(blob))
        book.active["C8"] = 0
        preview = read_preview(stream(book), self.scope, [self.item])
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["rows"][0]["change"], {"enrollment_id": 10, "version": 2, "solved": 0, "total": 12})
        self.assertEqual(preview["rows"][0]["status"], "변경")
        with self.assertRaisesRegex(ValueError, "다른 학원 또는 과제"):
            read_preview(stream(book), (1, 2, 4), [self.item])
        newer = {**self.item, "version": 3}
        self.assertIn("8행", read_preview(stream(book), self.scope, [newer])["errors"][0])

    def test_named_sheet_ambiguous_duplicate_and_invalid_cells(self):
        book = Workbook()
        sheet = book.active
        sheet.append(list(HEADERS))
        sheet.append([None, "수강생", None, None, "코멘트"])
        sheet.append([None, "수강생", 1, 2, None, 10])
        sheet.append([None, "수강생", 1, 2, None, 10])
        sheet.append([None, "수강생", "=1+1", 2, None, 11])
        sheet.append([None, "수강생", 20, 2, None, 11])
        items = [self.item, {**self.item, "enrollment_id": 11}]
        errors = read_preview(stream(book), self.scope, items)["errors"]
        self.assertEqual({message.split(" · ")[0] for message in errors}, {"2행", "3행", "4행", "5행", "6행"})
        preview = read_preview(stream(book), self.scope, [self.item])
        self.assertEqual(preview["rows"][0]["change"], {"enrollment_id": 10, "version": 2, "feedback": "코멘트"})


class HomeworkInspectionBatchTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Inspection", code="inspection", is_active=True)
        self.staff = get_user_model().objects.create_user(username="inspection-staff", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        self.factory = APIRequestFactory()
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title="검사", name="검사", subject="SCIENCE",
        )
        self.session = apps.get_model("lectures", "Session").objects.create(lecture=lecture, order=1, title="수업")
        self.homework = apps.get_model("homework_results", "Homework").objects.create(
            tenant=self.tenant, session=self.session, title="과제",
        )
        self.enrollments = []
        for i in range(2):
            user = get_user_model().objects.create_user(username=f"inspection-student-{i}", tenant=self.tenant)
            student = apps.get_model("students", "Student").objects.create(
                tenant=self.tenant, user=user, ps_number=f"HW{i}", omr_code=f"{i:08d}", name=f"학생{i}",
            )
            enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
                tenant=self.tenant, student=student, lecture=lecture,
            )
            apps.get_model("enrollment", "SessionEnrollment").objects.create(
                tenant=self.tenant, session=self.session, enrollment=enrollment,
            )
            apps.get_model("homework", "HomeworkAssignment").objects.create(
                tenant=self.tenant, homework=self.homework, session=self.session, enrollment=enrollment,
            )
            self.enrollments.append(enrollment)

    def save(self, rows):
        request = self.factory.post("/", {"rows": rows}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        return HomeworkInspectionView.as_view()(request, session_id=self.session.pk, homework_id=self.homework.pk)

    def test_atomic_conflict_and_student_specific_shared_feedback(self):
        first, second = self.enrollments
        change = {"enrollment_id": first.pk, "version": 0, "solved": 0, "total": 10, "feedback": "코멘트"}
        response = self.save([change])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"changed": 1})
        self.assertEqual(self.save([change]).data, {"changed": 0})
        response = self.save([
            {**change, "enrollment_id": second.pk},
            {**change, "solved": 3},
        ])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(HomeworkInspection.objects.values_list("enrollment_id", "solved", "version")), [(first.pk, 0, 1)])
        own = build_student_grades_summary(tenant=self.tenant, student=first.student)["homeworks"]
        self.assertEqual(own[0]["practice_inspection"], {"solved": 0, "total": 10, "feedback": "코멘트"})
        other = build_student_grades_summary(tenant=self.tenant, student=second.student)["homeworks"]
        self.assertFalse(any("practice_inspection" in row for row in other))
        apps.get_model("homework", "HomeworkAssignment").objects.filter(enrollment=first).delete()
        self.assertEqual(self.save([{**change, "version": 1}]).status_code, 409)
        hidden = build_student_grades_summary(tenant=self.tenant, student=first.student)["homeworks"]
        self.assertFalse(any("practice_inspection" in row for row in hidden))

    def test_home_rows_preserve_zero_inspection_for_absent_student_and_selected_parent(self):
        from apps.domains.student_app.results.views import MyGradesSummaryView

        enrollment = self.enrollments[0]
        student = enrollment.student
        TenantMembership.ensure_active(tenant=self.tenant, user=student.user, role="student")
        parent_user = get_user_model().objects.create_user(username="inspection-parent", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=parent_user, role="parent")
        student.parent = apps.get_model("parents", "Parent").objects.create(
            tenant=self.tenant, user=parent_user, name="Parent", phone="01000000001",
        )
        student.save(update_fields=["parent", "updated_at"])
        apps.get_model("attendance", "Attendance").objects.create(
            tenant=self.tenant, enrollment=enrollment, session=self.session, status="ABSENT",
        )
        self.assertEqual(self.save([{
            "enrollment_id": enrollment.pk, "version": 0,
            "solved": 0, "total": 15, "feedback": "Shared inspection",
        }]).status_code, 200)

        for role, user in (("student", student.user), ("parent", parent_user)):
            with self.subTest(role=role):
                responses = []
                for scope in ("", "rows"):
                    request = self.factory.get("/api/v1/student/grades/", {"scope": scope},
                                               HTTP_X_STUDENT_ID=str(student.pk))
                    request.tenant = self.tenant
                    force_authenticate(request, user=user)
                    response = MyGradesSummaryView.as_view()(request)
                    self.assertEqual(response.status_code, 200)
                    responses.append(response.data)
                full, rows = responses
                self.assertEqual(rows, {key: full[key] for key in ("exams", "homeworks")})
                self.assertEqual(len(rows["homeworks"]), 1)
                self.assertEqual(rows["homeworks"][0]["practice_inspection"], {
                    "solved": 0, "total": 15, "feedback": "Shared inspection",
                })
                self.assertEqual(rows["homeworks"][0]["enrollment_id"], enrollment.pk)
        # Analytics deliberately retains its previous independent projection:
        # an absent assignment without submission evidence is not resurrected.
        analytics_rows = build_student_grades_summary(
            tenant=self.tenant, student=student, include_grade_board_details=False,
        )
        self.assertEqual(analytics_rows["homeworks"], [])
        other = build_student_grades_summary(
            tenant=self.tenant, student=self.enrollments[1].student,
            include_grade_board_details=False, include_learning_todo_eligibility=True,
        )
        self.assertFalse(any("practice_inspection" in row for row in other["homeworks"]))
