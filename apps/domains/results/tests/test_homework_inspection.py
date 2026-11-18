from io import BytesIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook, load_workbook
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
