from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.apps import apps as django_apps
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.results.views import MyExamResultView, MyGradesSummaryView
from apps.domains.students.models import Student


User = get_user_model()


class MyGradesSummaryHomeworkTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="student-grades-hw", name="Student Grades HW", is_active=True)
        self.user = User.objects.create_user(
            username="student-grades-hw-user",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="SGH001",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="수학",
            name="수학",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.Exam = django_apps.get_model("exams", "Exam")
        self.ExamEnrollment = django_apps.get_model("exams", "ExamEnrollment")
        self.ExamQuestion = django_apps.get_model("exams", "ExamQuestion")
        self.Sheet = django_apps.get_model("exams", "Sheet")
        self.ExamAttempt = django_apps.get_model("results", "ExamAttempt")
        self.Result = django_apps.get_model("results", "Result")
        self.ResultItem = django_apps.get_model("results", "ResultItem")

    def _call(self):
        request = self.factory.get("/api/v1/student/grades/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyGradesSummaryView.as_view()(request)

    def _call_exam_result(self, exam_id: int):
        request = self.factory.get(f"/api/v1/student/results/me/exams/{exam_id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyExamResultView.as_view()(request, exam_id=exam_id)

    def _score_exam(
        self,
        *,
        title: str,
        order: int,
        score: float,
        max_score: float,
    ):
        session_order = order + 1
        session = Session.objects.create(
            lecture=self.lecture,
            order=session_order,
            regular_order=session_order,
            date=date(2026, 7, 1) + timedelta(days=order),
            title=f"{order}회",
        )
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=max_score,
            pass_score=max_score * 0.6,
        )
        exam.sessions.add(session)
        return self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=score,
            max_score=max_score,
        )

    def test_exam_trend_accumulates_rounds_and_normalizes_different_max_scores(self):
        self._score_exam(title="월간 테스트", order=1, score=16, max_score=20)
        self._score_exam(title="중간 테스트", order=2, score=45, max_score=50)
        self._score_exam(title="기말 테스트", order=3, score=0, max_score=100)

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [(row["round_index"], row["title"], row["score_pct"]) for row in response.data["exam_trend"]],
            [(1, "월간 테스트", 80.0), (2, "중간 테스트", 90.0), (3, "기말 테스트", 0.0)],
        )
        self.assertEqual(response.data["exam_summary"], {
            "scored_count": 3,
            "average_score_pct": 56.7,
            "latest_score_pct": 0.0,
            "change_pct_points": -90.0,
            "best_score_pct": 90.0,
        })
        self.assertTrue(all("recorded_at" in row for row in response.data["exam_trend"]))

    def test_foreign_and_template_exam_metadata_is_fail_closed(self):
        other_tenant = Tenant.objects.create(code="student-grades-foreign", name="Foreign", is_active=True)
        foreign_exam = self.Exam.objects.create(
            tenant=other_tenant,
            title="다른 학원 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        template_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="시험지 원본",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        for exam in (foreign_exam, template_exam):
            self.Result.objects.create(
                target_type="exam",
                target_id=exam.id,
                enrollment=self.enrollment,
                total_score=100,
                max_score=100,
            )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)

    def test_not_submitted_exam_stays_in_list_but_not_in_trend(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="미응시 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
            pass_score=60,
        )
        exam.sessions.add(self.session)
        attempt = self.ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"status": "NOT_SUBMITTED"},
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=0,
            max_score=100,
            attempt=attempt,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["achievement"], "NOT_SUBMITTED")
        self.assertIsNone(response.data["exams"][0]["total_score"])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)

    def test_duplicate_results_collapse_to_latest_exam_point(self):
        first = self._score_exam(
            title="중복 결과 시험",
            order=1,
            score=40,
            max_score=100,
        )
        other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="중복 연결 강의",
            name="중복 연결 강의",
            subject="MATH",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="중복 연결 1회",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        exam = self.Exam.objects.get(id=first.target_id)
        exam.sessions.add(other_session)
        self.Result.objects.create(
            target_type="exam",
            target_id=first.target_id,
            enrollment=other_enrollment,
            total_score=85,
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertEqual(len(response.data["exam_trend"]), 1)
        self.assertEqual(response.data["exam_trend"][0]["score_pct"], 85.0)

    def test_rank_cohort_excludes_corrupt_foreign_tenant_result(self):
        local_result = self._score_exam(
            title="테넌트 석차 테스트",
            order=1,
            score=80,
            max_score=100,
        )
        other_tenant = Tenant.objects.create(code="student-rank-foreign", name="Foreign", is_active=True)
        other_user = User.objects.create_user(
            username="student-rank-foreign-user",
            password="pw1234",
            tenant=other_tenant,
        )
        TenantMembership.ensure_active(tenant=other_tenant, user=other_user, role="student")
        other_student = Student.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="SRF001",
            omr_code="99998888",
            name="다른 학원 학생",
            phone="01099998888",
            parent_phone="01077776666",
        )
        other_lecture = Lecture.objects.create(
            tenant=other_tenant,
            title="다른 학원 수학",
            name="다른 학원 수학",
            subject="MATH",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=other_tenant,
            student=other_student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=local_result.target_id,
            enrollment=other_enrollment,
            total_score=100,
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["rank"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_size"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_avg"], 80.0)

    def test_rank_cohort_excludes_nonfinite_peer_and_response_renders(self):
        local_result = self._score_exam(
            title="석차 수치 안전 테스트",
            order=1,
            score=80,
            max_score=100,
        )
        peer_user = User.objects.create_user(
            username="student-rank-nonfinite-peer",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=peer_user,
            role="student",
        )
        peer_student = Student.objects.create(
            tenant=self.tenant,
            user=peer_user,
            ps_number="SRN001",
            omr_code="77776666",
            name="같은 학원 학생",
            phone="01077776666",
            parent_phone="01055554444",
        )
        peer_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=peer_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=local_result.target_id,
            enrollment=peer_enrollment,
            total_score=float("inf"),
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["cohort_size"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_avg"], 80.0)
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_assigned_unscored_homework_is_visible_as_not_submitted(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="미채점 과제",
            meta={"default_max_score": 20},
        )
        assignment = HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["homeworks"]), 1)
        row = response.data["homeworks"][0]
        self.assertEqual(row["homework_id"], homework.id)
        self.assertIsNone(row["score"])
        self.assertEqual(row["max_score"], 20.0)
        self.assertEqual(row["achievement"], "NOT_SUBMITTED")
        self.assertEqual(row["lecture_title"], "수학")
        self.assertEqual(row["recorded_at"], assignment.created_at.isoformat())

    def test_legacy_template_exam_items_are_included_in_summary_analysis(self):
        template_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="레거시 시험지",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        template_sheet = self.Sheet.objects.create(
            exam=template_exam,
            name="TEMPLATE",
            total_questions=1,
        )
        question = self.ExamQuestion.objects.create(
            sheet=template_sheet,
            number=7,
            score=100,
        )
        regular_exam = self.Exam.objects.create(
            tenant=self.tenant,
            template_exam=template_exam,
            title="레거시 운영 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        regular_exam.sessions.add(self.session)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=regular_exam.id,
            enrollment=self.enrollment,
            total_score=0,
            max_score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=question,
            answer="2",
            is_correct=False,
            score=0,
            max_score=100,
            source="manual",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["exams"][0]
        self.assertEqual(row["total_questions"], 1)
        self.assertEqual(row["wrong_count"], 1)
        self.assertEqual(row["wrong_question_numbers"], [7])

    def test_cross_tenant_template_items_are_excluded_from_summary_analysis(self):
        other_tenant = Tenant.objects.create(
            code="student-summary-template-foreign",
            name="Student Summary Template Foreign",
            is_active=True,
        )
        foreign_template = self.Exam.objects.create(
            tenant=other_tenant,
            title="외부 시험지",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        foreign_sheet = self.Sheet.objects.create(
            exam=foreign_template,
            name="FOREIGN_TEMPLATE",
            total_questions=1,
        )
        foreign_question = self.ExamQuestion.objects.create(
            sheet=foreign_sheet,
            number=91,
            score=100,
        )
        regular_exam = self.Exam.objects.create(
            tenant=self.tenant,
            template_exam=foreign_template,
            title="손상된 운영 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        regular_exam.sessions.add(self.session)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=regular_exam.id,
            enrollment=self.enrollment,
            total_score=100,
            max_score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=foreign_question,
            answer="1",
            is_correct=True,
            score=100,
            max_score=100,
            source="manual",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["exams"][0]
        self.assertEqual(row["total_questions"], 0)
        self.assertEqual(row["correct_count"], 0)
        self.assertEqual(row["wrong_question_numbers"], [])

    def test_removed_homework_assignment_is_hidden_from_student_summary(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제거된 과제",
            meta={"removed_from_session_at": "2026-05-24T00:00:00+09:00"},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_cross_lecture_homework_relations_are_fail_closed(self):
        other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="다른 반",
            name="다른 반",
            subject="MATH",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="다른 반 1회",
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=other_session,
            title="다른 반 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=other_session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=other_session,
            homework=homework,
            score=20,
            max_score=20,
            passed=True,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_nonfinite_homework_values_do_not_break_json_response(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="손상 수치 과제",
            meta={},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=float("inf"),
            max_score=float("inf"),
            passed=False,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["homeworks"][0]["achievement"], "NOT_SUBMITTED")
        self.assertIsNone(response.data["homeworks"][0]["max_score"])
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_inactive_enrollment_exam_result_is_hidden_from_student_detail(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=100,
            max_score=100,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 404)

    def test_student_detail_filters_corrupt_foreign_tenant_result_item(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="문항 격리 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )
        other_tenant = Tenant.objects.create(
            code="student-detail-item-foreign",
            name="Student Detail Item Foreign",
            is_active=True,
        )
        foreign_exam = self.Exam.objects.create(
            tenant=other_tenant,
            title="외부 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        foreign_sheet = self.Sheet.objects.create(
            exam=foreign_exam,
            name="FOREIGN",
            total_questions=1,
        )
        foreign_question = self.ExamQuestion.objects.create(
            sheet=foreign_sheet,
            number=99,
            score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=foreign_question,
            answer="1",
            is_correct=True,
            score=100,
            max_score=100,
            source="manual",
        )

        response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["analysis"]["total_questions"], 0)

    def test_inactive_enrollment_scores_are_hidden_from_student_summary(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 성적",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="비활성 수강 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=18,
            max_score=20,
            passed=True,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["homeworks"], [])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)
