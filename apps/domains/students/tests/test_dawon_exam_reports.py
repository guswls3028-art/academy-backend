from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture
from apps.domains.results.services.exam_analysis_metrics import load_exam_analysis
from apps.domains.students.models import ClassGroup, ClassGroupLecture, DawonExamReport, Student
from apps.domains.students.services.dawon_exam_reports import admin_exam_detail, report_for_student


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonExamReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        cls.user = get_user_model().objects.create_user(tenant=cls.tenant, username="t719_report_learner")
        cls.student = Student.objects.create(pk=2, tenant=cls.tenant, user=cls.user, name="다원 수업 검수 학생 2",
                                             school_type="HIGH", high_school="샘플고", ps_number="DW-SAMPLE")
        group = ClassGroup.objects.create(tenant=cls.tenant, name="고1 HYPER 연합반", kind="union", track="HYPER")
        for title, period, school in [("HYPER 방학 수학Ⅱ", "vacation", ""), ("샘플고 중간", "midterm", "샘플고")]:
            course = Lecture.objects.create(tenant=cls.tenant, title=title, name="담당", subject="수학")
            ClassGroupLecture.objects.create(group=group, lecture=course, period=period, school=school)
        call_command("seed_dawon_reports", stdout=StringIO())

    def test_invalid_cross_tenant_student_link_never_enters_statistics(self):
        from apps.domains.exams.models import ExamEnrollment
        other = Tenant.objects.create(code="qa-outside-reports", name="검수")
        account = get_user_model().objects.create_user(tenant=other, username="outside-report")
        outside = Student.objects.create(tenant=other, user=account, name="외부", ps_number="qa-outside")
        report = DawonExamReport.objects.select_related("exam", "session__lecture").first()
        invalid = Enrollment.objects.create(tenant=self.tenant, lecture=report.session.lecture, student=outside, status="ACTIVE")
        ExamEnrollment.objects.create(exam=report.exam, enrollment=invalid)
        data = admin_exam_detail(self.tenant, report.exam, report.session.lecture_id, self.user)
        self.assertNotIn(invalid.pk, [row["id"] for row in data["roster"]])
        self.assertTrue(all(invalid.pk not in item["graded_ids"] for item in data["questions"]))

    def test_three_reports_and_seed_replay(self):
        call_command("seed_dawon_reports", stdout=StringIO())
        self.assertEqual(DawonExamReport.objects.count(), 10)
        for kind, counts, points in [("level_pair", [10, 5], 2), ("cycle", [20], 3), ("school_mock", [10], 3)]:
            with self.subTest(kind=kind):
                definition = DawonExamReport.objects.filter(kind=kind).order_by("-sequence", "pk").first()
                exam, lesson = definition.exam, definition.session
                enrollment = Enrollment.objects.get(tenant=self.tenant, lecture=lesson.lecture, student=self.student)
                analysis = load_exam_analysis(exam=exam, tenant=self.tenant, lecture_id=lesson.lecture_id, include_trends=False)
                report = report_for_student(tenant=self.tenant, exam=exam, session=lesson, enrollment=enrollment, analysis=analysis)
                self.assertEqual([len(s["questions"]) for s in report["sections"]], counts)
                self.assertEqual(len(report["trend"]), points)
                self.assertTrue(all(row["exam_id"] for row in report["trend"]))
                self.assertTrue(report["homework"])
                self.assertIsInstance(report["sections"][0]["questions"][0]["correct"], bool)
                self.assertGreaterEqual(report["sections"][0]["questions"][0]["correct_rate"], 0)
                staff = admin_exam_detail(self.tenant, exam, lesson.lecture_id, self.user)
                self.assertEqual(staff["assigned"], 22 if kind == "school_mock" else 33)
                self.assertEqual(sum(staff["distribution"]), staff["graded"])
                roster_ids = {row["id"] for row in staff["roster"]}
                for item in staff["questions"]:
                    self.assertLessEqual(set(item["correct_ids"]), set(item["graded_ids"]))
                    self.assertLessEqual(set(item["graded_ids"]), roster_ids)
                for section in report["sections"]:
                    for item in section["questions"]:
                        self.assertNotIn("graded_ids", item)
                        self.assertNotIn("correct_ids", item)
                    if section["exam_id"] == exam.pk:
                        for own, aggregate in zip(section["questions"], staff["questions"], strict=True):
                            self.assertEqual(own["correct"], enrollment.pk in aggregate["correct_ids"])
                            self.assertEqual(own["correct_rate"], 100 * len(aggregate["correct_ids"]) / len(aggregate["graded_ids"]))
                self.assertTrue(all(row["student_id"] for row in staff["roster"]))
                exam.student_results_published = False
                exam.save(update_fields=["student_results_published"])
                self.assertIsNone(report_for_student(tenant=self.tenant, exam=exam, session=lesson,
                                                    enrollment=enrollment, analysis=analysis))
