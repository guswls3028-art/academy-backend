"""
시험↔클리닉 드리프트 해소 및 resolution lifecycle 회귀 테스트

검증 포인트:
1. 드리프트 해소: 세션에 시험 여러 개 + 일부 불합격 → exam_passed=False
2. ClinicLink meta 누적: auto_create_if_failed → auto_create_if_exam_risk 시
   kinds 배열에 EXAM_FAILED/EXAM_RISK 둘 다 보존
3. carry_over → resolution_type=CARRIED_OVER (WAIVED 아님)
4. resolution_history append-only 이력
5. submit_exam_retake: 이미 해소된 link → ValueError
6. submit_exam_retake/homework_retake: 음수 점수 → ValueError
7. completed_at 불변 (미완료 회귀해도 유지)
8. legacy fallback: meta.exam_id 매칭 실패 시 skip (과매칭 방지)
"""
from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture, Session as LectureSession
from apps.domains.progress.models import (
    ClinicLink,
    ProgressPolicy,
    SessionProgress,
)
from apps.domains.progress.services.clinic_remediation_service import (
    ClinicRemediationService,
)
from apps.domains.progress.services.clinic_resolution_service import (
    ClinicResolutionService,
)
from apps.domains.progress.services.clinic_trigger_service import (
    ClinicTriggerService,
)

User = get_user_model()


class DriftResolutionTest(TestCase, ClinicTestMixin):
    """Phase 2: 드리프트 해소 관련 테스트"""

    def setUp(self):
        self.data = self.setup_full_tenant("drift", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.lecture = self.data["lecture"]

    def test_session_not_completed_when_any_exam_failed(self):
        """세션에 시험 2개, 1개 통과/1개 불합격 → exam_passed=False"""
        from apps.domains.results.models import Result
        exam_a = Exam.objects.create(tenant=self.tenant, title="A", pass_score=60.0, max_score=100.0)
        exam_b = Exam.objects.create(tenant=self.tenant, title="B", pass_score=60.0, max_score=100.0)
        exam_a.sessions.add(self.lec_session)
        exam_b.sessions.add(self.lec_session)

        Result.objects.create(
            target_type="exam", target_id=exam_a.id,
            enrollment=self.enrollment, total_score=80, max_score=100,
        )
        Result.objects.create(
            target_type="exam", target_id=exam_b.id,
            enrollment=self.enrollment, total_score=40, max_score=100,
        )

        # exam 범위에 session.order=1 포함하도록 policy 명시 (defaults는 order=2 이상만)
        policy, _ = ProgressPolicy.objects.get_or_create(
            lecture=self.lecture,
            defaults={
                "exam_pass_source": ProgressPolicy.ExamPassSource.EXAM,
                "exam_start_session_order": 1,
                "homework_start_session_order": 1,
            },
        )
        from apps.domains.progress.services.session_calculator import (
            SessionProgressCalculator,
        )
        sp = SessionProgressCalculator.calculate(
            enrollment_id=self.enrollment.id,
            session=self.lec_session,
            attendance_type="online",
            video_progress_rate=100,
        )
        self.assertFalse(sp.exam_passed, "MAX 전략이어도 개별 불합격 있으면 passed=False")
        self.assertFalse(sp.completed, "일부 시험 불합격 → 세션 미완료")
        self.assertIn("all_passed", sp.exam_meta)
        self.assertFalse(sp.exam_meta["all_passed"])

    def test_missing_result_blocks_session_completion(self):
        """세션에 시험 2개, Result는 1개만 있어도 세션 완료로 판정되면 안 됨.

        시험 A에 응시 + 합격, 시험 B는 Result 없음(미응시/채점 미입력) 상태에서
        이전에는 per_exam_rows가 A 1건만 생성되어 `all(passed)`=True로 세션 완료.
        수정 후: per_exam_rows는 exam_ids 전체(2건) 포함하고 B는 passed=False(no_result)
        → exam_passed=False, 세션 완료 차단.
        """
        from apps.domains.results.models import Result
        exam_a = Exam.objects.create(tenant=self.tenant, title="A", pass_score=60.0, max_score=100.0)
        exam_b = Exam.objects.create(tenant=self.tenant, title="B", pass_score=60.0, max_score=100.0)
        exam_a.sessions.add(self.lec_session)
        exam_b.sessions.add(self.lec_session)

        Result.objects.create(
            target_type="exam", target_id=exam_a.id,
            enrollment=self.enrollment, total_score=80, max_score=100,
        )
        # exam_b에는 Result 없음 (누락 상태)

        ProgressPolicy.objects.get_or_create(
            lecture=self.lecture,
            defaults={
                "exam_pass_source": ProgressPolicy.ExamPassSource.EXAM,
                "exam_start_session_order": 1,
                "homework_start_session_order": 1,
            },
        )
        from apps.domains.progress.services.session_calculator import (
            SessionProgressCalculator,
        )
        sp = SessionProgressCalculator.calculate(
            enrollment_id=self.enrollment.id,
            session=self.lec_session,
            attendance_type="online",
            video_progress_rate=100,
        )
        self.assertFalse(
            sp.exam_passed,
            "exam_b Result 누락 → 전수 검사에서 passed=False여야 함",
        )
        self.assertFalse(sp.completed, "Result 누락 시험이 있으면 세션 완료 불가")
        exams_meta = sp.exam_meta.get("exams", [])
        self.assertEqual(len(exams_meta), 2, "exam_ids 전체가 per_exam_rows에 포함")
        b_row = next((x for x in exams_meta if x["exam_id"] == exam_b.id), None)
        self.assertIsNotNone(b_row)
        self.assertTrue(b_row.get("no_result"))
        self.assertFalse(b_row.get("passed"))
        self.assertTrue(sp.exam_meta.get("missing_results"))

    def test_completed_at_preserved_on_regression(self):
        """completed=True → 점수 수정으로 False 회귀해도 completed_at 유지"""
        from apps.domains.results.models import Result
        exam = Exam.objects.create(tenant=self.tenant, title="E", pass_score=60.0, max_score=100.0)
        exam.sessions.add(self.lec_session)
        r = Result.objects.create(
            target_type="exam", target_id=exam.id,
            enrollment=self.enrollment, total_score=80, max_score=100,
        )
        # exam 범위에 session.order=1 포함
        ProgressPolicy.objects.get_or_create(
            lecture=self.lecture,
            defaults={
                "exam_start_session_order": 1,
                "homework_start_session_order": 1,
                "exam_pass_source": ProgressPolicy.ExamPassSource.EXAM,
            },
        )

        from apps.domains.progress.services.session_calculator import (
            SessionProgressCalculator,
        )
        sp = SessionProgressCalculator.calculate(
            enrollment_id=self.enrollment.id,
            session=self.lec_session,
            attendance_type="online",
            video_progress_rate=100,
        )
        self.assertTrue(sp.completed)
        self.assertIsNotNone(sp.completed_at)
        first_completed_at = sp.completed_at

        # 점수 하락 (회귀)
        r.total_score = 40
        r.save(update_fields=["total_score", "updated_at"])

        sp2 = SessionProgressCalculator.calculate(
            enrollment_id=self.enrollment.id,
            session=self.lec_session,
            attendance_type="online",
            video_progress_rate=100,
        )
        self.assertFalse(sp2.completed)
        self.assertEqual(
            sp2.completed_at, first_completed_at,
            "completed_at은 최초 완료 시점으로 불변",
        )


class AutoCreateMetaMergeTest(TestCase, ClinicTestMixin):
    """Phase 1: auto_create meta 덮어쓰기 차단"""

    def setUp(self):
        self.data = self.setup_full_tenant("meta", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]

    def test_exam_risk_preserves_failed_evidence(self):
        """auto_create_if_failed → auto_create_if_exam_risk 호출 시 근거 보존"""
        # 첫 단계: EXAM_FAILED 링크 생성
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type="exam",
            source_id=101,
            meta={
                "kind": "EXAM_FAILED",
                "kinds": ["EXAM_FAILED"],
                "exam_id": 101,
                "score": 42.0,
                "pass_score": 60.0,
            },
        )

        # evaluate mock: LOW_CONFIDENCE_OMR 감지
        from unittest.mock import patch
        fake_reasons = {"LOW_CONFIDENCE_OMR": {"count": 3, "threshold": 2}}
        with patch(
            "apps.domains.progress.services.clinic_trigger_service.ClinicExamRuleService.evaluate",
            return_value=fake_reasons,
        ):
            ClinicTriggerService.auto_create_if_exam_risk(
                enrollment_id=self.enrollment.id,
                session=self.lec_session,
                exam_id=101,
            )

        link.refresh_from_db()
        self.assertIn("EXAM_FAILED", link.meta["kinds"], "기존 EXAM_FAILED kind 보존")
        self.assertIn("EXAM_RISK", link.meta["kinds"])
        self.assertEqual(link.meta.get("score"), 42.0, "기존 score 보존")
        self.assertEqual(link.meta.get("pass_score"), 60.0, "기존 pass_score 보존")
        self.assertEqual(link.meta.get("exam_reasons"), fake_reasons)


class CarriedOverResolutionTest(TestCase, ClinicTestMixin):
    """Phase 3: CARRIED_OVER enum + history"""

    def setUp(self):
        self.data = self.setup_full_tenant("carry", student_count=1)
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]

    def test_carry_over_sets_carried_over_type(self):
        """carry_over → resolution_type=CARRIED_OVER (WAIVED 아님)"""
        link = self.make_clinic_link(
            self.enrollment, self.lec_session,
            source_type="exam", source_id=1,
        )
        new_link = ClinicResolutionService.carry_over(clinic_link_id=link.id)
        link.refresh_from_db()
        self.assertEqual(link.resolution_type, ClinicLink.ResolutionType.CARRIED_OVER)
        self.assertNotEqual(link.resolution_type, ClinicLink.ResolutionType.WAIVED)
        self.assertEqual(new_link.cycle_no, 2)
        # history 기록 확인
        self.assertTrue(link.resolution_history)
        self.assertEqual(link.resolution_history[-1]["action"], "carry_over")

    def test_unresolve_preserves_evidence_in_history(self):
        """unresolve 시 이전 evidence가 history에 누적"""
        link = self.make_clinic_link(
            self.enrollment, self.lec_session,
            source_type="exam", source_id=1,
        )
        ClinicResolutionService.resolve_manually(
            clinic_link_id=link.id, user_id=1, memo="테스트",
        )
        link.refresh_from_db()
        self.assertIsNotNone(link.resolved_at)

        ClinicResolutionService.unresolve(clinic_link_id=link.id)
        link.refresh_from_db()
        self.assertIsNone(link.resolved_at)
        # history에 이전 MANUAL_OVERRIDE + evidence 보존
        actions = [h["action"] for h in (link.resolution_history or [])]
        self.assertIn("resolve_manual", actions)
        self.assertIn("unresolve", actions)
        prev = next(
            h for h in link.resolution_history
            if h["action"] == "unresolve"
        )
        self.assertEqual(prev["prev_resolution_type"], "MANUAL_OVERRIDE")
        self.assertEqual(prev["prev_evidence"].get("memo"), "테스트")


class RemediationValidationTest(TestCase, ClinicTestMixin):
    """Phase 1: 점수 검증 & 중복 요청 방어"""

    def setUp(self):
        self.data = self.setup_full_tenant("remed", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.admin = User.objects.create_user(
            username="admin_remed", password="x",
        )

    def test_exam_retake_negative_score_rejected(self):
        """음수 점수 → ValueError"""
        exam = Exam.objects.create(
            tenant=self.tenant, title="N", max_score=100.0, pass_score=60.0,
        )
        link = self.make_clinic_link(
            self.enrollment, self.lec_session,
            source_type="exam", source_id=exam.id,
        )
        with self.assertRaises(ValueError) as ctx:
            ClinicRemediationService.submit_exam_retake(
                clinic_link_id=link.id, score=-5.0,
                graded_by_user_id=self.admin.id,
            )
        self.assertIn("0 이상", str(ctx.exception))

    def test_resolved_link_retake_rejected(self):
        """이미 해소된 link에 retake 요청 → ValueError"""
        exam = Exam.objects.create(
            tenant=self.tenant, title="R", max_score=100.0, pass_score=60.0,
        )
        link = self.make_clinic_link(
            self.enrollment, self.lec_session,
            source_type="exam", source_id=exam.id,
        )
        # 수동 해소
        ClinicResolutionService.resolve_manually(
            clinic_link_id=link.id, user_id=self.admin.id,
        )
        with self.assertRaises(ValueError) as ctx:
            ClinicRemediationService.submit_exam_retake(
                clinic_link_id=link.id, score=80.0,
                graded_by_user_id=self.admin.id,
            )
        self.assertIn("이미 해소", str(ctx.exception))


class StudentResultRemediatedTest(TestCase, ClinicTestMixin):
    """
    student_result_service.get_my_exam_result_data의 remediated/final_pass 판정이
    EXAM_PASS + MANUAL_OVERRIDE 모두 커버하는지 검증.

    드리프트 재발 방지: admin_student_grades_view / student_app.results.views는
    MANUAL_OVERRIDE를 REMEDIATED로 분류하므로, 시험 상세 뷰도 동일하게 맞춰야 한다.
    """

    def setUp(self):
        from apps.domains.exams.models import Exam, ExamEnrollment
        from apps.domains.results.models import Result

        self.data = self.setup_full_tenant("remed_detail", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.student_user = self.enrollment.student.user

        self.exam = Exam.objects.create(
            tenant=self.tenant, title="MidTerm",
            max_score=100.0, pass_score=60.0,
            allow_retake=False, max_attempts=1,
        )
        self.exam.sessions.add(self.lec_session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        # 1차 불합격 Result
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment,
            total_score=40, max_score=100,
        )

        self.link = self.make_clinic_link(
            self.enrollment, self.lec_session,
            source_type="exam", source_id=self.exam.id,
        )
        self.admin = User.objects.create_user(
            username="admin_remed_detail", password="x",
        )

    def _fetch(self):
        from unittest.mock import MagicMock
        from apps.domains.results.services.student_result_service import (
            get_my_exam_result_data,
        )
        request = MagicMock()
        request.user = self.student_user
        request.tenant = self.tenant
        return get_my_exam_result_data(request, self.exam.id, tenant=self.tenant)

    def test_exam_pass_resolution_sets_remediated(self):
        """EXAM_PASS(재시험 통과)로 해소되면 remediated=True, final_pass=True"""
        ClinicResolutionService.resolve_by_exam_pass(
            enrollment_id=self.enrollment.id,
            session_id=self.lec_session.id,
            exam_id=self.exam.id,
            score=80.0, pass_score=60.0,
        )
        data = self._fetch()
        self.assertFalse(data["is_pass"], "1차는 불합격")
        self.assertTrue(data["remediated"])
        self.assertTrue(data["final_pass"])
        self.assertIsNotNone(data["clinic_retake"])

    def test_manual_override_resolution_sets_remediated(self):
        """MANUAL_OVERRIDE(관리자 수동 해소)도 remediated=True — 드리프트 재발 방지"""
        ClinicResolutionService.resolve_manually(
            clinic_link_id=self.link.id,
            user_id=self.admin.id,
            memo="수동 해소 회귀 테스트",
        )
        data = self._fetch()
        self.assertFalse(data["is_pass"], "1차는 불합격")
        self.assertTrue(
            data["remediated"],
            "MANUAL_OVERRIDE 해소도 remediated로 인식돼야 함",
        )
        self.assertTrue(
            data["final_pass"],
            "MANUAL_OVERRIDE 해소 → 학생 상세에서도 최종 합격으로 보여야 함",
        )

    def test_waived_resolution_is_not_remediated(self):
        """WAIVED(면제)는 remediated에 포함되지 않음 (목록 뷰 정책과 일치)"""
        ClinicResolutionService.waive(
            clinic_link_id=self.link.id,
            user_id=self.admin.id,
            memo="면제",
        )
        data = self._fetch()
        self.assertFalse(data["remediated"])
        self.assertFalse(data["final_pass"])


class LegacyFallbackTest(TestCase, ClinicTestMixin):
    """Phase 1: legacy fallback 과매칭 차단"""

    def setUp(self):
        self.data = self.setup_full_tenant("legacy", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]

    def test_legacy_without_matching_exam_id_is_skipped(self):
        """source_type=NULL + meta.exam_id 매칭 없으면 해소하지 않음"""
        # legacy 링크: exam_id 미상
        legacy = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type=None,
            source_id=None,
            meta={"note": "legacy — no exam_id"},
        )
        count = ClinicResolutionService.resolve_by_exam_pass(
            enrollment_id=self.enrollment.id,
            session_id=self.lec_session.id,
            exam_id=999,
        )
        self.assertEqual(count, 0, "meta.exam_id 매칭 실패 → skip")
        legacy.refresh_from_db()
        self.assertIsNone(legacy.resolved_at)

    def test_legacy_with_matching_exam_id_is_resolved(self):
        """source_type=NULL + meta.exam_id=999 → 999 통과 시 해소"""
        legacy = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type=None,
            source_id=None,
            meta={"exam_id": 999, "score": 40, "pass_score": 60},
        )
        count = ClinicResolutionService.resolve_by_exam_pass(
            enrollment_id=self.enrollment.id,
            session_id=self.lec_session.id,
            exam_id=999,
            score=80.0, pass_score=60.0,
        )
        self.assertEqual(count, 1)
        legacy.refresh_from_db()
        self.assertIsNotNone(legacy.resolved_at)
        self.assertEqual(legacy.resolution_type, "EXAM_PASS")
