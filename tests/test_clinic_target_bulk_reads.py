"""Clinic target projection stays bounded without changing score/source semantics."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.exams.models import Exam, ExamLecturePolicy
from apps.domains.homework.models import HomeworkAssignment, HomeworkPolicy
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.progress.models import ClinicLink
from apps.domains.results.models import ExamAttempt, Result, ResultFact
from apps.domains.results.services.clinic_target_service import ClinicTargetService


class ClinicTargetBulkReadTests(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("target-bulk")
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]

    def add_sources(self, index, enrollment=None):
        enrollment = enrollment or self.enrollment
        session = self.make_lecture_session(self.data["lecture"], order=index + 2)
        exam = Exam.objects.create(tenant=self.tenant, title=f"Exam {index}", pass_score=80, max_score=100)
        exam.sessions.add(session)
        ExamLecturePolicy.objects.create(exam=exam, lecture=self.data["lecture"], pass_score=70)
        attempt = ExamAttempt.objects.create(
            exam=exam, enrollment=enrollment, attempt_index=1, status="done", meta={"total_score": 20},
        )
        Result.objects.create(
            target_type="exam", target_id=exam.id, enrollment=enrollment, attempt=attempt,
            total_score=20, max_score=100,
        )
        link = self.make_clinic_link(enrollment, session, source_id=exam.id)
        homework = Homework.objects.create(
            tenant=self.tenant, session=session, title=f"Homework {index}",
            cutline_mode="COUNT" if index % 2 else None,
            cutline_value=15 if index % 2 else None,
        )
        HomeworkPolicy.objects.create(tenant=self.tenant, session=session, cutline_mode="PERCENT", cutline_value=60)
        HomeworkAssignment.objects.create(tenant=self.tenant, session=session, homework=homework, enrollment=enrollment)
        HomeworkScore.objects.create(
            homework=homework, session=session, enrollment=enrollment, score=5, max_score=20,
        )
        self.make_clinic_link(enrollment, session, source_type="homework", source_id=homework.id)
        return session, exam, attempt, link

    def targets(self, **kwargs):
        return ClinicTargetService.list_admin_targets(tenant=self.tenant, **kwargs)

    def test_700_mixed_targets_do_not_add_queries_per_source_or_student(self):
        self.add_sources(0)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(len(self.targets()), 2)
        enrollment = self.enrollment
        for index in range(1, 350):
            if index % 10 == 0:
                student = self.make_student(self.tenant, str(index))
                enrollment = self.make_enrollment(self.tenant, student, self.data["lecture"])
            self.add_sources(index, enrollment)
        before_links = list(ClinicLink.objects.values())
        with CaptureQueriesContext(connection) as large:
            rows = self.targets()
        self.assertEqual(len(rows), 700)
        print(f"Clinic targets: 2 rows={len(small)} queries; 700 rows={len(large)} queries")
        self.assertEqual(len(large), len(small), f"2 rows={len(small)} queries; 700 rows={len(large)} queries")
        self.assertLessEqual(len(large), 45)
        self.assertEqual(list(ClinicLink.objects.values()), before_links)
        self.assertTrue(all(row["exam_score"] == 20 and row["cutline_score"] == 70 for row in rows if row["source_type"] == "exam"))
        for row in rows:
            if row["source_type"] == "homework":
                self.assertEqual(row["homework_score"], 5)
                self.assertEqual(row["homework_cutline"], 15 if row["homework_cutline_mode"] == "COUNT" else 12)

    def test_confidence_uses_initial_attempt_and_latest_200_non_null_facts(self):
        _, exam, attempt, link = self.add_sources(0)
        def fact(meta):
            return ResultFact(
                target_type="exam", target_id=exam.id, enrollment=self.enrollment,
                attempt=attempt, submission_id=1, question_id=1, source="omr", meta=meta,
            )
        ResultFact.objects.bulk_create([
            fact({"grading": {"invalid_reason": "LOW_CONFIDENCE"}}),
            *[fact({}) for _ in range(200)],
            fact(None),
        ])
        self.assertEqual(next(row for row in self.targets() if row["clinic_link_id"] == link.id)["reason"], "score")
        fact({"grading": {"invalid_reason": "low_confidence"}}).save()
        self.assertEqual(next(row for row in self.targets() if row["clinic_link_id"] == link.id)["reason"], "confidence")
        ResultFact.objects.all().delete()
        attempt.meta = {"total_score": 20, "grading": {"invalid_reason": "AMBIGUOUS_SINGLE"}}
        attempt.is_representative = False
        attempt.save()
        retake = ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment, attempt_index=2, status="done", meta={"total_score": 90},
        )
        Result.objects.filter(target_id=exam.id).update(attempt=retake, total_score=90)
        row = next(row for row in self.targets() if row["clinic_link_id"] == link.id)
        self.assertEqual(row["reason"], "confidence")
        self.assertEqual(row["exam_score"], 20)
        self.assertEqual([entry["score"] for entry in row["attempt_history"]], [20, 90])

    def test_legacy_smallest_live_exam_and_resolved_history_are_preserved(self):
        excluded = [
            Exam.objects.create(tenant=self.tenant, title="Archived", is_active=False),
            Exam.objects.create(tenant=self.tenant, title="Template", exam_type="template"),
            Exam.objects.create(tenant=self.make_tenant("foreign-target"), title="Foreign"),
        ]
        session, exam, _, link = self.add_sources(0)
        legacy = self.make_clinic_link(self.enrollment, session, source_type=None, source_id=None)
        for extra in excluded:
            extra.sessions.add(session)
        foreign_link = self.make_clinic_link(self.enrollment, session, source_id=excluded[-1].id)
        row = next(row for row in self.targets() if row["clinic_link_id"] == legacy.id)
        self.assertEqual(row["exam_id"], exam.id)
        self.assertEqual(row["exam_score"], 20)
        self.assertNotIn(foreign_link.id, {row["clinic_link_id"] for row in self.targets(include_resolved=True)})
        link.resolved_at = timezone.now()
        link.resolution_type = "MANUAL_PASS"
        link.resolution_evidence = {"memo": "Teacher confirmed"}
        link.save()
        self.assertNotIn(link.id, {row["clinic_link_id"] for row in self.targets()})
        resolved = next(row for row in self.targets(include_resolved=True) if row["clinic_link_id"] == link.id)
        self.assertEqual(resolved["resolution_evidence"], {"memo": "Teacher confirmed"})
        self.assertEqual(resolved["exam_score"], 20)
        with self.assertNumQueries(0):
            self.assertEqual(ClinicTargetService.list_admin_targets(tenant=None), [])

    def test_explicit_missing_rows_also_bulk_load_cutlines_without_creating_links(self):
        def add_missing(index):
            session, _, attempt, link = self.add_sources(index)
            SessionEnrollment.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment)
            attempt.meta = {"status": "NOT_SUBMITTED"}
            attempt.save(update_fields=["meta"])
            link.delete()
        add_missing(0)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(len(self.targets()), 2)
        for index in range(1, 16):
            add_missing(index)
        with CaptureQueriesContext(connection) as large:
            rows = self.targets()
        self.assertEqual(len(large), len(small))
        missing = [row for row in rows if row["source_type"] == "exam"]
        self.assertEqual(len(missing), 16)
        self.assertTrue(all(row["reason"] == "missing" and row["exam_score"] is None and row["cutline_score"] == 70 for row in missing))
        self.assertFalse(ClinicLink.objects.filter(source_type="exam").exists())
