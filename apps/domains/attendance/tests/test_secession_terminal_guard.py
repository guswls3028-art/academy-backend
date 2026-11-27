from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.attendance.services import create_attendance_roster
from unittest.mock import patch


User = get_user_model()
Attendance = apps.get_model("attendance", "Attendance")
Enrollment = apps.get_model("enrollment", "Enrollment")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Student = apps.get_model("students", "Student")
SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")


class AttendanceSecessionTerminalGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="att-terminal", name="Attendance Terminal", is_active=True)
        self.admin = User.objects.create_user(
            username="att-terminal-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Terminal Lecture",
            name="Terminal Lecture",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1회차")
        student_user = User.objects.create_user(
            username="att-terminal-student",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="ATTTERM001",
            omr_code="87654321",
            name="퇴원학생",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="INACTIVE",
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            status="SECESSION",
        )

    def _patch(self, data, *, row=None):
        row = row or self.attendance
        request = self.factory.patch(
            f"/api/v1/lectures/attendance/{row.id}/",
            data,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AttendanceViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=row.id,
        )

    def test_full_withdrawal_then_one_online_session_limits_assessments(self):
        from apps.domains.exams.models import Exam, ExamEnrollment
        from apps.domains.homework.models import HomeworkAssignment
        from apps.domains.homework.views.homework_assignment_view import HomeworkAssignmentManageView
        from apps.domains.homework_results.models import Homework
        from apps.domains.parents.models import Parent
        from apps.domains.results.models import Result
        from apps.domains.student_app.exams.views import StudentExamListView
        from apps.support.attendance.learning_todo_eligibility import (
            eligible_session_enrollments,
            exam_is_learning_todo_eligible,
        )
        from apps.support.exams.view_dependencies import active_enrollment_ids_for_exam_assignment
        from apps.support.student_app.exam_dependencies import student_exam_queryset

        enrollment = self.enrollment
        enrollment.status = "ACTIVE"
        enrollment.save(update_fields=["status"])
        rows = [self.attendance]
        for order in (2, 3):
            session = Session.objects.create(lecture=enrollment.lecture, order=order, title=f"QA {order}")
            rows.append(
                Attendance.objects.create(tenant=self.tenant, enrollment=enrollment, session=session, status="PRESENT")
            )
        unique_exams, homework = [], []
        for row in rows:
            SessionEnrollment.objects.create(tenant=self.tenant, enrollment=enrollment, session=row.session)
            exam = Exam.objects.create(tenant=self.tenant, title="주간 평가", exam_type="regular", is_active=True)
            exam.sessions.add(row.session)
            unique_exams.append(exam)
            homework.append(
                Homework.objects.create(tenant=self.tenant, session=row.session, title=f"Worksheet {row.pk}")
            )
        shared = Exam.objects.create(tenant=self.tenant, title="Shared", exam_type="regular", is_active=True)
        shared.sessions.add(rows[0].session, rows[2].session)
        grade = Result.objects.create(
            target_type="exam", target_id=unique_exams[0].pk, enrollment=enrollment, total_score=81, max_score=100
        )
        withdrawn = self._patch(
            {"status": "SECESSION", "confirm_secession": True, "secession_scope": "lecture"}, row=rows[1]
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        for _ in range(2):
            restored = self._patch({"status": "ONLINE", "restore_scope": "lecture"}, row=rows[2])
            self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(
            set(ExamEnrollment.objects.filter(enrollment=enrollment).values_list("exam_id", flat=True)),
            {unique_exams[2].pk, shared.pk},
        )
        self.assertEqual(
            list(HomeworkAssignment.objects.filter(enrollment=enrollment).values_list("homework_id", flat=True)),
            [homework[2].pk],
        )
        # A retained assessment target may predate the withdrawal. A stored grade
        # remains readable while a historical or ungraded exam cannot be taken.
        for exam in unique_exams[:2]:
            ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        for i, row in enumerate(rows):
            expected = i == 2
            with self.subTest(order=i + 1, entry="assignment pickers"):
                with self.assertNumQueries(1):
                    self.assertEqual(
                        active_enrollment_ids_for_exam_assignment(tenant=self.tenant, session=row.session),
                        [enrollment.pk] if expected else [],
                    )
                self.assertEqual(
                    eligible_session_enrollments(tenant=self.tenant, session_id=row.session_id).exists(), expected
                )
                path = f"/api/v1/homework/assignments/?homework_id={homework[i].pk}"
                assign = self.factory.put(path, {"enrollment_ids": [enrollment.pk]}, format="json")
                assign.tenant = self.tenant
                force_authenticate(assign, user=self.admin)
                assigned = HomeworkAssignmentManageView.as_view()(assign)
                self.assertEqual(assigned.status_code, 200 if expected else 400, assigned.data)
                self.assertEqual(
                    HomeworkAssignment.objects.filter(homework=homework[i], enrollment=enrollment).count(),
                    int(expected),
                )
            with self.subTest(order=i + 1, entry="student action"):
                self.assertEqual(
                    exam_is_learning_todo_eligible(
                        tenant_id=self.tenant.pk, enrollment_id=enrollment.pk, exam_id=unique_exams[i].pk
                    ),
                    expected,
                )
        projection = {exam.pk: exam for exam in student_exam_queryset(enrollment.student, self.tenant)}
        with self.subTest(entry="history and actionable sessions"):
            self.assertEqual(set(projection), {unique_exams[0].pk, unique_exams[2].pk, shared.pk})
            self.assertFalse(projection[unique_exams[0].pk].learning_todo_eligible)
            self.assertTrue(projection[shared.pk].learning_todo_eligible)
            self.assertEqual(projection[shared.pk].student_session_id, rows[2].session_id)
        student = enrollment.student
        TenantMembership.ensure_active(tenant=self.tenant, user=student.user, role="student")
        parent = User.objects.create_user(username="att-terminal-parent", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=parent, role="parent")
        student.parent = Parent.objects.create(
            tenant=self.tenant, user=parent, name="QA parent", phone=student.parent_phone
        )
        student.save(update_fields=["parent"])
        for user, selection in [(student.user, {}), (parent, {"HTTP_X_STUDENT_ID": str(student.pk)})]:
            request = self.factory.get("/api/v1/student/exams/", **selection)
            request.tenant = self.tenant
            force_authenticate(request, user=user)
            response = StudentExamListView.as_view()(request)
            self.assertEqual(response.status_code, 200, response.data)
            items = {item["id"]: item for item in response.data["items"]}
            self.assertEqual(set(items), {unique_exams[0].pk, unique_exams[2].pk, shared.pk})
            self.assertFalse(items[unique_exams[0].pk]["learning_todo_eligible"])
            self.assertTrue(items[shared.pk]["learning_todo_eligible"])
            self.assertEqual(items[shared.pk]["session_id"], rows[2].session_id)
        grade.refresh_from_db()
        self.assertEqual(grade.total_score, 81)
        withdrawn_again = self._patch(
            {
                "status": "SECESSION",
                "confirm_secession": True,
                "secession_scope": "session",
            },
            row=rows[2],
        )
        self.assertEqual(withdrawn_again.status_code, 200, withdrawn_again.data)
        self.assertFalse(ExamEnrollment.objects.filter(exam=shared, enrollment=enrollment).exists())
        self.assertTrue(Result.objects.filter(pk=grade.pk, total_score=81).exists())

    def test_patch_cannot_revert_secession_to_attendance_status(self):
        response = self._patch({"status": "PRESENT"})

        self.assertEqual(response.status_code, 409, response.data)
        self.attendance.refresh_from_db()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.attendance.status, "SECESSION")
        self.assertEqual(self.enrollment.status, "INACTIVE")

    def test_memo_only_patch_preserves_secession(self):
        response = self._patch({"memo": "퇴원 상담 완료"})

        self.assertEqual(response.status_code, 200, response.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, "SECESSION")
        self.assertEqual(self.attendance.memo, "퇴원 상담 완료")

    def test_full_put_obeys_withdrawal_confirmation_scope_and_restore_retry(self):
        from apps.domains.exams.models import Exam, ExamEnrollment
        from apps.domains.fees.models import FeeTemplate, StudentFee
        from apps.domains.homework.models import HomeworkAssignment
        from apps.domains.homework_results.models import Homework

        session = self.attendance.session
        exam = Exam.objects.create(tenant=self.tenant, title="Exam", exam_type="regular", is_active=True)
        exam.sessions.add(session)
        homework = Homework.objects.create(tenant=self.tenant, session=session, title="Worksheet")
        template = FeeTemplate.objects.create(tenant=self.tenant, lecture=self.enrollment.lecture, name="Tuition",
                                              fee_type="tuition", billing_cycle="monthly", amount=100_000, auto_assign=True)
        self.assertEqual(self._patch({"status": "PRESENT", "restore_scope": "lecture"}).status_code, 200)
        fee = StudentFee.objects.get(enrollment=self.enrollment, fee_template=template)
        exams = ExamEnrollment.objects.filter(enrollment=self.enrollment, exam=exam)
        assignments = HomeworkAssignment.objects.filter(enrollment=self.enrollment, homework=homework)
        other = Session.objects.create(lecture=self.enrollment.lecture, order=2, title="2회차")
        sibling = Attendance.objects.create(tenant=self.tenant, session=other, enrollment=self.enrollment, status="ONLINE")
        member = SessionEnrollment.objects.filter(tenant=self.tenant, enrollment=self.enrollment,
                                                  session_id=self.attendance.session_id)

        def put(status, **fields):
            request = self.factory.put("/attendance/", {
                "session": self.attendance.session_id, "enrollment_id": self.enrollment.pk,
                "status": status, **fields,
            }, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            return AttendanceViewSet.as_view({"put": "update"})(request, pk=self.attendance.pk)

        for fields in ({}, {"confirm_secession": True, "secession_scope": "unknown"},
                       {"confirm_secession": True, "secession_scope": "lecture", "session": other.pk}):
            self.assertEqual(put("SECESSION", memo="저장 금지", **fields).status_code, 400)
            self.attendance.refresh_from_db()
            self.assertEqual((self.attendance.status, self.attendance.memo), ("PRESENT", ""))
            self.assertTrue(member.exists())
            self.assertTrue(exams.exists() and assignments.exists())
            fee.refresh_from_db()
            self.assertTrue(fee.is_active)
        for _ in range(2):
            self.assertEqual(put("SECESSION", confirm_secession=True, secession_scope="session",
                                 memo="퇴원 메모").status_code, 200)
            self.assertFalse(member.exists())
            self.assertFalse(exams.exists() or assignments.exists())
        self.assertEqual(put("SECESSION", memo="보관 메모").status_code, 200)
        self.assertEqual(put("ONLINE").status_code, 409)
        self.assertEqual(put("ONLINE", restore_scope="lecture").status_code, 409)
        for _ in range(2):
            self.assertEqual(put("ONLINE", restore_scope="session").status_code, 200)
            self.assertEqual(member.count(), 1)
            self.assertEqual((exams.count(), assignments.count()), (1, 1))
        self.attendance.refresh_from_db()
        self.assertEqual((self.attendance.status, self.attendance.memo), ("ONLINE", "보관 메모"))
        self.assertEqual(put("LATE").status_code, 200)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, "LATE")
        for _ in range(2):
            self.assertEqual(put("SECESSION", confirm_secession=True, secession_scope="lecture").status_code, 200)
        fee.refresh_from_db()
        self.assertFalse(fee.is_active)
        self.assertEqual(put("ONLINE", restore_scope="lecture").status_code, 200)
        fee.refresh_from_db()
        self.assertTrue(fee.is_active)
        self.enrollment.refresh_from_db()
        sibling.refresh_from_db()
        self.assertEqual((self.enrollment.status, sibling.status), ("ACTIVE", "SECESSION"))

    def test_confirmed_lecture_restore_saves_one_status_and_can_be_retried(self):
        other_session = Session.objects.create(lecture=self.enrollment.lecture, order=2, title="다른 차시")
        other = Attendance.objects.create(tenant=self.tenant, session=other_session, enrollment=self.enrollment, status="SECESSION")
        self.attendance.memo = "상담 기록"
        self.attendance.save(update_fields=["memo"])
        payload = {"status": "PRESENT", "restore_scope": "lecture"}
        for _ in range(2):
            response = self._patch(payload)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["enrollment_status"], "ACTIVE")
            self.assertEqual(response.data["id"], self.attendance.id)
        self.attendance.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.attendance.status, self.attendance.memo), ("PRESENT", "상담 기록"))
        self.assertEqual(other.status, "SECESSION")
        self.assertEqual(SessionEnrollment.objects.filter(enrollment=self.enrollment, session=self.attendance.session).count(), 1)
        self.assertEqual(self._patch({"status": "ABSENT", "restore_scope": "lecture"}).status_code, 409)
        SessionEnrollment.objects.filter(enrollment=self.enrollment, session=self.attendance.session).delete()
        self.assertEqual(self._patch(payload).status_code, 409)

    def test_session_restore_requires_the_reviewed_scope(self):
        self.assertEqual(self._patch({"status": "LATE", "restore_scope": "session"}).status_code, 409)
        self.enrollment.status = "ACTIVE"
        self.enrollment.save(update_fields=["status"])
        self.assertEqual(self._patch({"status": "LATE", "restore_scope": "lecture"}).status_code, 409)
        response = self._patch({"status": "LATE", "restore_scope": "session"})
        self.assertEqual(response.status_code, 200, response.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, "LATE")

    def test_invalid_request_and_late_failure_preserve_withdrawal(self):
        self.assertEqual(self._patch({"status": "UNKNOWN", "restore_scope": "lecture"}).status_code, 400)
        with patch("apps.domains.attendance.views.reconcile_learning_todo_targets", side_effect=ValidationError("failed")):
            self.assertEqual(self._patch({"status": "ONLINE", "restore_scope": "lecture"}).status_code, 400)
        self.enrollment.refresh_from_db()
        self.attendance.refresh_from_db()
        self.assertEqual((self.enrollment.status, self.attendance.status), ("INACTIVE", "SECESSION"))
        self.assertFalse(SessionEnrollment.objects.filter(enrollment=self.enrollment).exists())

    def test_bulk_roster_cannot_reactivate_inactive_enrollment_or_assign_fees(self):
        with patch(
            "apps.domains.attendance.services.roster.auto_assign_roster_fees"
        ) as assign_fees:
            with self.assertRaises(ValidationError):
                create_attendance_roster(
                    tenant=self.tenant,
                    session_id=self.attendance.session_id,
                    student_ids=[self.enrollment.student_id],
                )

        assign_fees.assert_not_called()
        self.enrollment.refresh_from_db()
        self.attendance.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        self.assertEqual(self.attendance.status, "SECESSION")
