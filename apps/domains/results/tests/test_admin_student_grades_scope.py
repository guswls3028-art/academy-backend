from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.clinic.tests import ClinicTestMixin
from apps.core.models import TenantMembership
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.views.admin_student_grades_view import (
    AdminStudentGradesView,
    _build_exam_progression,
)


User = get_user_model()
_DEFAULT_TENANT = object()


class AdminStudentGradesScopeTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.data = self.setup_full_tenant("student-grades-scope", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.admin_user = User.objects.create_user(
            username="student_grades_scope_admin",
            password="test1234",
            is_staff=True,
            is_superuser=True,
        )
        if hasattr(self.admin_user, "tenant_id"):
            self.admin_user.tenant_id = self.tenant.id
            self.admin_user.save(update_fields=["tenant_id"])
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin_user,
            role="admin",
        )

    def _get(self, student_id, *, user=None, tenant=_DEFAULT_TENANT):
        request = self.factory.get(
            "/results/admin/student-grades/",
            {"student_id": student_id},
        )
        request.tenant = self.tenant if tenant is _DEFAULT_TENANT else tenant
        force_authenticate(request, user=user or self.admin_user)
        return AdminStudentGradesView.as_view()(request)

    def _score(
        self,
        *,
        title: str,
        order: int,
        score: float,
        max_score: float,
        submitted_offset: int = 0,
        is_active: bool = True,
        not_submitted: bool = False,
    ) -> tuple[object, Result]:
        session_model = self.data["lec_session"].__class__
        exam_model = self.data["lec_session"].exams.model
        if order == 1:
            session = self.data["lec_session"]
            session.title = "1차시"
            session.date = date(2026, 7, 1)
            session.save(update_fields=["title", "date"])
        else:
            session = session_model.objects.create(
                lecture=self.data["lecture"],
                order=order,
                title=f"{order}차시",
                date=date(2026, 7, order),
            )
        exam = exam_model.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=exam_model.ExamType.REGULAR,
            is_active=is_active,
            pass_score=max_score * 0.6,
            max_score=max_score,
        )
        exam.sessions.add(session)
        attempt = None
        if not_submitted:
            attempt = ExamAttempt.objects.create(
                exam=exam,
                enrollment=self.data["enrollments"][0],
                attempt_index=1,
                is_representative=True,
                status="done",
                meta={"status": "NOT_SUBMITTED"},
            )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.data["enrollments"][0],
            attempt=attempt,
            total_score=score,
            max_score=max_score,
            submitted_at=timezone.now() + timedelta(days=submitted_offset),
        )
        return exam, result

    def _member(self, *, suffix: str, role: str, tenant=None, active: bool = True):
        user = User.objects.create_user(
            username=f"student_grades_{suffix}",
            password="test1234",
        )
        membership = TenantMembership.ensure_active(
            tenant=tenant or self.tenant,
            user=user,
            role=role,
        )
        if not active:
            membership.is_active = False
            membership.save(update_fields=["is_active"])
        return user

    def test_invalid_student_id_returns_400(self):
        response = self._get("abc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "student_id must be integer")

    def test_active_same_tenant_student_returns_empty_payload_when_no_scores(self):
        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["homeworks"], [])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)

    def test_permission_matrix_is_bound_to_active_tenant_staff_membership(self):
        teacher = self._member(suffix="teacher", role="teacher")
        student_user = self.student.user
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=student_user,
            role="student",
        )
        inactive_teacher = self._member(
            suffix="inactive_teacher",
            role="teacher",
            active=False,
        )
        other = self.setup_full_tenant("student-grades-permission-other", student_count=1)
        foreign_teacher = self._member(
            suffix="foreign_teacher",
            role="teacher",
            tenant=other["tenant"],
        )

        self.assertEqual(self._get(self.student.id, user=teacher).status_code, 200)
        self.assertEqual(self._get(self.student.id, user=student_user).status_code, 403)
        self.assertEqual(self._get(self.student.id, user=inactive_teacher).status_code, 403)
        self.assertEqual(self._get(self.student.id, user=foreign_teacher).status_code, 403)
        self.assertEqual(self._get(self.student.id, tenant=None).status_code, 403)

    def test_cross_tenant_student_returns_404(self):
        other = self.setup_full_tenant("student-grades-other", student_count=1)
        other_student = other["students"][0]

        response = self._get(other_student.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "student not found")

    def test_soft_deleted_same_tenant_student_returns_404(self):
        self.student.deleted_at = timezone.now()
        self.student.save(update_fields=["deleted_at"])

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "student not found")

    def test_exam_results_auto_accumulate_in_academic_order_and_normalize_max_scores(self):
        first_exam, _ = self._score(
            title="Ymath 주간 테스트 1회",
            order=1,
            score=40,
            max_score=50,
            submitted_offset=10,
            is_active=False,
        )
        self._score(
            title="Ymath 주간 테스트 2회",
            order=2,
            score=90,
            max_score=100,
            submitted_offset=1,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["title"] for row in response.data["exam_trend"]],
            ["Ymath 주간 테스트 1회", "Ymath 주간 테스트 2회"],
        )
        self.assertEqual(
            [row["round_index"] for row in response.data["exam_trend"]],
            [1, 2],
        )
        self.assertEqual(
            [row["score_pct"] for row in response.data["exam_trend"]],
            [80.0, 90.0],
        )
        self.assertTrue(response.data["exam_trend"][0]["archived"])
        self.assertEqual(response.data["exam_trend"][0]["exam_id"], first_exam.id)
        self.assertEqual(response.data["exam_summary"]["average_score_pct"], 85.0)
        self.assertEqual(response.data["exam_summary"]["change_pct_points"], 10.0)

        self._score(
            title="Ymath 주간 테스트 3회",
            order=3,
            score=48,
            max_score=50,
        )
        refreshed = self._get(self.student.id)

        self.assertEqual(
            [row["round_index"] for row in refreshed.data["exam_trend"]],
            [1, 2, 3],
        )
        self.assertEqual(refreshed.data["exam_summary"]["latest_score_pct"], 96.0)
        self.assertEqual(refreshed.data["exam_summary"]["best_score_pct"], 96.0)

    def test_not_submitted_is_null_in_list_and_excluded_from_trend(self):
        self._score(
            title="Ymath 주간 테스트 미응시",
            order=2,
            score=0,
            max_score=100,
            not_submitted=True,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertIsNone(response.data["exams"][0]["total_score"])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)

    def test_corrupt_foreign_exam_target_is_not_exposed(self):
        other = self.setup_full_tenant("student-grades-corrupt-target", student_count=1)
        exam_model = other["lec_session"].exams.model
        foreign_exam = exam_model.objects.create(
            tenant=other["tenant"],
            title="다른 학원 비공개 테스트",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
        )
        foreign_exam.sessions.add(other["lec_session"])
        Result.objects.create(
            target_type="exam",
            target_id=foreign_exam.id,
            enrollment=self.data["enrollments"][0],
            total_score=100,
            max_score=100,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["exam_trend"], [])

    def test_corrupt_foreign_attempt_status_is_ignored(self):
        local_exam, local_result = self._score(
            title="로컬 정상 시험",
            order=2,
            score=80,
            max_score=100,
        )
        other = self.setup_full_tenant("student-grades-corrupt-attempt", student_count=1)
        exam_model = other["lec_session"].exams.model
        foreign_exam = exam_model.objects.create(
            tenant=other["tenant"],
            title="다른 학원 미응시 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
        )
        foreign_attempt = ExamAttempt.objects.create(
            exam=foreign_exam,
            enrollment=other["enrollments"][0],
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"status": "NOT_SUBMITTED"},
        )
        local_result.attempt = foreign_attempt
        local_result.save(update_fields=["attempt"])

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["exam_id"], local_exam.id)
        self.assertEqual(response.data["exams"][0]["total_score"], 80)
        self.assertIsNone(response.data["exams"][0]["meta_status"])
        self.assertEqual(response.data["exams"][0]["achievement"], "PASS")
        self.assertEqual(response.data["exam_summary"]["scored_count"], 1)

    def test_multi_lecture_exam_uses_result_enrollment_lecture_and_system_policy(self):
        self.data["lecture"].is_system = True
        self.data["lecture"].save(update_fields=["is_system"])
        normal_lecture = self.make_lecture(self.tenant, title="Ymath 정상반")
        normal_session = self.make_lecture_session(normal_lecture, order=2, title="정상반 2차시")
        normal_session.date = date(2026, 7, 2)
        normal_session.save(update_fields=["date"])
        normal_enrollment = self.make_enrollment(
            self.tenant,
            self.student,
            normal_lecture,
        )
        exam_model = self.data["lec_session"].exams.model
        shared_exam = exam_model.objects.create(
            tenant=self.tenant,
            title="여러 강의 공용 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
        )
        shared_exam.sessions.add(self.data["lec_session"], normal_session)
        Result.objects.create(
            target_type="exam",
            target_id=shared_exam.id,
            enrollment=normal_enrollment,
            total_score=88,
            max_score=100,
            submitted_at=timezone.now() - timedelta(minutes=1),
        )
        # 더 최신인 시스템 강의 결과가 같은 시험의 정상 결과를 suppress하면 안 된다.
        Result.objects.create(
            target_type="exam",
            target_id=shared_exam.id,
            enrollment=self.data["enrollments"][0],
            total_score=100,
            max_score=100,
            submitted_at=timezone.now(),
        )

        system_sessionless_exam = exam_model.objects.create(
            tenant=self.tenant,
            title="시스템 강의 세션없는 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
        )
        Result.objects.create(
            target_type="exam",
            target_id=system_sessionless_exam.id,
            enrollment=self.data["enrollments"][0],
            total_score=99,
            max_score=100,
            submitted_at=timezone.now() + timedelta(minutes=1),
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["title"] for row in response.data["exams"]], ["여러 강의 공용 시험"])
        self.assertEqual(response.data["exams"][0]["lecture_id"], normal_lecture.id)
        self.assertEqual(response.data["exams"][0]["session_id"], normal_session.id)

    def test_corrupt_enrollment_foreign_lecture_metadata_is_not_exposed(self):
        other = self.setup_full_tenant("student-grades-corrupt-enrollment", student_count=1)
        enrollment = self.data["enrollments"][0]
        enrollment.lecture = other["lecture"]
        enrollment.save(update_fields=["lecture"])
        exam_model = self.data["lec_session"].exams.model
        local_exam = exam_model.objects.create(
            tenant=self.tenant,
            title="로컬 세션없는 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
        )
        Result.objects.create(
            target_type="exam",
            target_id=local_exam.id,
            enrollment=enrollment,
            total_score=90,
            max_score=100,
            submitted_at=timezone.now(),
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["exam_trend"], [])

    def test_homework_metadata_is_scoped_through_homework_and_session_tenant(self):
        score_model = self.data["enrollments"][0].homework_scores.model
        homework_model = score_model._meta.get_field("homework").remote_field.model
        local_homework = homework_model.objects.create(
            tenant=self.tenant,
            homework_type=homework_model.HomeworkType.REGULAR,
            session=self.data["lec_session"],
            title="로컬 정상 과제",
        )
        score_model.objects.create(
            enrollment=self.data["enrollments"][0],
            session=self.data["lec_session"],
            homework=local_homework,
            score=10,
            max_score=10,
            passed=True,
        )

        other = self.setup_full_tenant("student-grades-corrupt-homework", student_count=1)
        foreign_homework = homework_model.objects.create(
            tenant=other["tenant"],
            homework_type=homework_model.HomeworkType.REGULAR,
            session=other["lec_session"],
            title="다른 학원 비공개 과제",
        )
        score_model.objects.create(
            enrollment=self.data["enrollments"][0],
            session=other["lec_session"],
            homework=foreign_homework,
            score=100,
            max_score=100,
            passed=True,
        )
        # 같은 로컬 enrollment/homework를 쓰더라도 다른 tenant session의
        # 손상된 재시도 row가 정상 과제의 재응시 횟수를 부풀리면 안 된다.
        score_model.objects.create(
            enrollment=self.data["enrollments"][0],
            session=other["lec_session"],
            homework=local_homework,
            attempt_index=2,
            score=10,
            max_score=10,
            passed=True,
        )
        system_lecture = self.make_lecture(self.tenant, title="시스템 영상함")
        system_lecture.is_system = True
        system_lecture.save(update_fields=["is_system"])
        system_session = self.make_lecture_session(
            system_lecture,
            order=1,
            title="시스템 차시",
        )
        score_model.objects.create(
            enrollment=self.data["enrollments"][0],
            session=system_session,
            homework=local_homework,
            attempt_index=3,
            score=10,
            max_score=10,
            passed=True,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["title"] for row in response.data["homeworks"]],
            ["로컬 정상 과제"],
        )
        self.assertEqual(response.data["homeworks"][0]["retake_count"], 1)

    def test_non_finite_homework_is_excluded_and_response_json_still_renders(self):
        score_model = self.data["enrollments"][0].homework_scores.model
        homework_model = score_model._meta.get_field("homework").remote_field.model
        homework = homework_model.objects.create(
            tenant=self.tenant,
            homework_type=homework_model.HomeworkType.REGULAR,
            session=self.data["lec_session"],
            title="손상된 무한대 과제",
        )
        score_model.objects.create(
            enrollment=self.data["enrollments"][0],
            session=self.data["lec_session"],
            homework=homework,
            score=float("inf"),
            max_score=10,
            passed=False,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["homeworks"], [])
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_latest_recorded_result_wins_same_exam_across_enrollments(self):
        other_lecture = self.make_lecture(self.tenant, title="Ymath 중복수강반")
        other_session = self.make_lecture_session(other_lecture, order=2, title="중복 2차시")
        other_enrollment = self.make_enrollment(
            self.tenant,
            self.student,
            other_lecture,
        )
        exam_model = self.data["lec_session"].exams.model
        exam = exam_model.objects.create(
            tenant=self.tenant,
            title="중복 결과 정렬 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
        )
        exam.sessions.add(self.data["lec_session"], other_session)
        stale = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=other_enrollment,
            total_score=10,
            max_score=100,
            submitted_at=None,
        )
        Result.objects.filter(id=stale.id).update(
            created_at=timezone.now() - timedelta(days=2),
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.data["enrollments"][0],
            total_score=90,
            max_score=100,
            submitted_at=timezone.now() - timedelta(days=1),
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertEqual(response.data["exams"][0]["total_score"], 90)

    def test_progression_excludes_invalid_numbers_but_keeps_zero_and_bonus_scores(self):
        def row(exam_id, score, max_score):
            return {
                "exam_id": exam_id,
                "enrollment_id": 1,
                "title": f"시험 {exam_id}",
                "total_score": score,
                "max_score": max_score,
                "recorded_at": f"2026-07-{exam_id:02d}T00:00:00+00:00",
            }

        trend, summary = _build_exam_progression([
            row(1, 0, 100),
            row(2, 120, 100),
            row(3, -1, 100),
            row(4, float("nan"), 100),
            row(5, float("inf"), 100),
            row(6, 50, float("inf")),
            row(7, 50, 0),
        ])

        self.assertEqual([point["score_pct"] for point in trend], [0.0, 120.0])
        self.assertEqual(summary["scored_count"], 2)
        self.assertEqual(summary["average_score_pct"], 60.0)
        self.assertEqual(summary["best_score_pct"], 120.0)

    def test_non_finite_result_is_excluded_and_response_json_still_renders(self):
        self._score(
            title="손상된 무한대 점수",
            order=2,
            score=float("inf"),
            max_score=100,
        )

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["exam_trend"], [])
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_student_grades_query_count_does_not_scale_per_exam(self):
        self._score(title="쿼리 테스트 1회", order=2, score=70, max_score=100)
        with CaptureQueriesContext(connection) as initial_queries:
            first_response = self._get(self.student.id)
        self.assertEqual(first_response.status_code, 200)

        for order in range(3, 8):
            self._score(
                title=f"쿼리 테스트 {order - 1}회",
                order=order,
                score=70 + order,
                max_score=100,
            )
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded_response = self._get(self.student.id)

        self.assertEqual(expanded_response.status_code, 200)
        self.assertEqual(len(expanded_response.data["exam_trend"]), 6)
        self.assertLessEqual(len(expanded_queries), len(initial_queries) + 2)
