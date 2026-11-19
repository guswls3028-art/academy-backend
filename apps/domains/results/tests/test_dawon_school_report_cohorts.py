from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core.models import Tenant
from apps.domains.results.models import StudentReportedScore
from apps.domains.results.tests import test_school_report_collection as collection


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonSchoolReportTests(TestCase):
    user = collection.SchoolReportCollectionTests.user
    make_student = collection.SchoolReportCollectionTests.make_student
    call = collection.SchoolReportCollectionTests.call
    put = collection.SchoolReportCollectionTests.put

    def setUp(self):
        self.factory = collection.APIRequestFactory()
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.teacher = self.user(self.tenant, "teacher", "teacher")
        self.student = self.make_student(self.tenant, "own", "첫 학생")
        self.peer = self.make_student(self.tenant, "second", "둘째 학생")
        self.term = collection.TERM

    def cohorts(self, **extra):
        return self.call("school-report-roster", self.teacher, query={**self.term, "view": "cohorts", **extra})

    @patch("django.utils.timezone.localdate", return_value=date(2026, 11, 19))
    def test_term_averages_normalize_keep_historical_scope_and_drilldown(self, _date):
        self.assertEqual(self.put([{"subject":"수학", "score":"7.5", "max_score":"10"}]).status_code, 200)
        self.student.high_school = "전학"
        self.student.grade = 3
        self.student.save()
        self.put([{"subject":"수학", "score":8, "max_score":10}, {"subject":"영어", "score":70, "max_score":100}])
        self.call("school-report-me", self.peer.user, method="put", query=self.term,
                  body={"subjects":[{"subject":"수학", "score":50, "max_score":100}]})
        data = self.cohorts(cohort_school="가고", cohort_grade=2).data
        self.assertEqual(len(data["cohorts"]), 1)
        cohort = data["cohorts"][0]
        self.assertEqual((cohort["school"], cohort["grade"], cohort["student_count"]), ("가고", 2, 2))
        self.assertEqual({v["subject"]: float(v["average"]) for v in cohort["subjects"]}, {"수학":65, "영어":70})
        self.assertEqual({v["id"] for v in data["students"]}, {self.student.id, self.peer.id})
        StudentReportedScore.objects.filter(student=self.peer).update(school_at_submission=None, year_grade_at_submission=None)
        unknown = self.cohorts(cohort_school="", cohort_grade="").data
        self.assertEqual([s["id"] for s in unknown["students"]], [self.peer.id])
        self.assertEqual(len(unknown["cohorts"]), 2)
        previous = {**self.term, "academic_year":2025}
        self.put([collection.MATH], query=previous)
        self.assertIsNone(StudentReportedScore.objects.get(student=self.student, academic_year=2025).year_grade_at_submission)
        query = {**self.term, "view":"cohorts"}
        self.assertEqual(self.call("school-report-roster", self.student.user, query=query).status_code, 403)
        self.assertEqual(self.cohorts(cohort_school="가고").status_code, 400)
        self.assertEqual(self.cohorts(academic_year=2024).data["cohorts"], [])
        with override_settings(DAWON_REVIEW_CLASS_GROUPS=False):
            self.assertEqual(self.cohorts().status_code, 404)
