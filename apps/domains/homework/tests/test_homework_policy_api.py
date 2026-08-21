from __future__ import annotations

from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.students.models import Student
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore, Homework


class HomeworkPolicyApiTests(APITestCase):
    def setUp(self):
        # TenantMiddleware는 Host 기반 + (localhost는 X-Tenant-Code 허용) 이므로
        # 테스트에서는 localhost + X-Tenant-Code 조합으로 강제한다.
        self.tenant = Tenant.objects.create(
            name="Local Tenant",
            code="9999",
            is_active=True,
        )

        User = get_user_model()
        self.user = User.objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_admin",
            is_active=True,
            is_staff=True,
        )
        self.user.set_password("pass1234!")
        self.user.save(update_fields=["password"])

        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role="admin",
            is_active=True,
        )

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="S1",
        )

        User = get_user_model()
        student_user = User.objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_student",
            is_active=True,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Test Student",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )

        self.client.force_authenticate(user=self.user)
        self.req_headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }

    def test_get_policy_by_session_creates_and_returns_policy(self):
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        self.assertEqual(res.status_code, 200, res.data)

        data = res.data
        results = data["results"] if isinstance(data, dict) and "results" in data else data
        self.assertTrue(isinstance(results, list) and len(results) == 1, results)

        p = results[0]
        self.assertEqual(int(p["session"]), int(self.session.id))
        self.assertIn(p["cutline_mode"], ("PERCENT", "COUNT"))
        self.assertTrue(isinstance(p["cutline_value"], int))

    def test_patch_policy_updates_fields(self):
        # First GET ensures policy exists
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        pid = res.data["results"][0]["id"]

        res2 = self.client.patch(
            f"/api/v1/homework/policies/{pid}/",
            {"cutline_mode": "PERCENT", "cutline_value": 70, "round_unit_percent": 5},
            format="json",
            **self.req_headers,
        )
        self.assertEqual(res2.status_code, 200, res2.data)
        self.assertEqual(res2.data["cutline_mode"], "PERCENT")
        self.assertEqual(int(res2.data["cutline_value"]), 70)

    def test_title_only_patch_preserves_session_cutline_default(self):
        self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="기존 과제",
        )
        detail = self.client.get(
            f"/api/v1/homeworks/{homework.id}/",
            **self.req_headers,
        )
        self.assertTrue(detail.data["uses_session_cutline_default"])

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"title": "이름만 변경"},
            format="json",
            HTTP_X_EXPECTED_UPDATED_AT=detail.data["updated_at"],
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["uses_session_cutline_default"])
        homework.refresh_from_db()
        self.assertIsNone(homework.cutline_mode)
        self.assertIsNone(homework.cutline_value)

    def test_homework_patch_rejects_stale_version(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="동시 수정 과제",
        )
        detail = self.client.get(
            f"/api/v1/homeworks/{homework.id}/",
            **self.req_headers,
        )
        Homework.objects.filter(pk=homework.id).update(
            title="다른 선생님 수정",
            updated_at=timezone.now() + timedelta(seconds=1),
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"title": "오래된 화면 수정"},
            format="json",
            HTTP_X_EXPECTED_UPDATED_AT=detail.data["updated_at"],
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "stale_resource")
        homework.refresh_from_db()
        self.assertEqual(homework.title, "다른 선생님 수정")

    def test_patch_policy_recalculates_existing_homework_scores(self):
        # ensure policy exists
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        pid = res.data["results"][0]["id"]

        hw = Homework.objects.create(tenant=self.tenant, session=self.session, title="HW1")
        hs = HomeworkScore.objects.create(
            enrollment_id=self.enrollment.id,
            session=self.session,
            homework=hw,
            score=60.0,       # percent 입력
            max_score=None,
            passed=False,     # intentionally wrong (should become True after cutline=50)
            clinic_required=False,
        )

        res2 = self.client.patch(
            f"/api/v1/homework/policies/{pid}/",
            {"cutline_mode": "PERCENT", "cutline_value": 50, "round_unit_percent": 5},
            format="json",
            **self.req_headers,
        )
        self.assertEqual(res2.status_code, 200, res2.data)

        hs.refresh_from_db()
        self.assertTrue(hs.passed)
        self.assertEqual(hs.max_score, 100.0)

    def test_raising_policy_cutline_creates_a_homework_clinic_link(self):
        ClinicLink = apps.get_model("progress", "ClinicLink")
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        pid = res.data["results"][0]["id"]
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="43점 과제",
            meta={"default_max_score": 43},
        )
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=30,
            max_score=100,
            passed=True,
            clinic_required=False,
        )

        response = self.client.patch(
            f"/api/v1/homework/policies/{pid}/",
            {"cutline_mode": "PERCENT", "cutline_value": 80},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        score.refresh_from_db()
        self.assertEqual(score.max_score, 43.0)
        self.assertFalse(score.passed)
        self.assertTrue(score.clinic_required)
        self.assertTrue(
            ClinicLink.objects.filter(
                enrollment=self.enrollment,
                session=self.session,
                source_type="homework",
                source_id=homework.id,
                resolved_at__isnull=True,
            ).exists()
        )

    def test_raw_score_cutline_cannot_exceed_a_homework_max_score(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="20점 과제",
            meta={"default_max_score": 20},
        )
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        pid = res.data["results"][0]["id"]

        response = self.client.patch(
            f"/api/v1/homework/policies/{pid}/",
            {"cutline_mode": "COUNT", "cutline_value": 30},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("cutline_value", response.data)
        homework.refresh_from_db()
        self.assertEqual(homework.meta["default_max_score"], 20)

    def test_updating_homework_max_score_syncs_existing_primary_scores(self):
        ClinicLink = apps.get_model("progress", "ClinicLink")
        policy_res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        self.assertEqual(policy_res.status_code, 200, policy_res.data)
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="43점 과제",
            meta={"default_max_score": 100},
        )
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=41,
            max_score=100,
            passed=False,
            clinic_required=True,
        )
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            source_type="homework",
            source_id=homework.id,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"max_score": 43},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["max_score"], 43.0)
        score.refresh_from_db()
        self.assertEqual(score.max_score, 43.0)
        self.assertTrue(score.passed)
        self.assertFalse(score.clinic_required)
        link.refresh_from_db()
        self.assertIsNotNone(link.resolved_at)

    def test_homework_max_score_cannot_be_lower_than_an_existing_score(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="보존 과제",
            meta={"default_max_score": 100},
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=41,
            max_score=100,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"max_score": 40},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 400, response.data)
        homework.refresh_from_db()
        self.assertEqual(homework.meta["default_max_score"], 100)

    def test_resaving_configured_max_score_repairs_a_legacy_score_snapshot(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="기존 43점 과제",
            meta={"default_max_score": 43},
        )
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=41,
            max_score=100,
            passed=False,
            clinic_required=True,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"max_score": 43},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        score.refresh_from_db()
        self.assertEqual(score.max_score, 43.0)
        self.assertTrue(score.passed)
        self.assertFalse(score.clinic_required)

    def test_unrelated_meta_patch_preserves_the_configured_max_score(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="메타 보존 과제",
            meta={"default_max_score": 43},
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"meta": {"due_date": "2026-08-05"}},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        homework.refresh_from_db()
        self.assertEqual(homework.meta["default_max_score"], 43)
        self.assertEqual(homework.meta["due_date"], "2026-08-05")

    def test_homeworks_in_same_session_can_use_different_cutlines(self):
        inherited = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="기본 기준 과제",
        )
        easier = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="개별 기준 과제",
        )
        inherited_score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=inherited,
            score=75,
            max_score=100,
            passed=False,
            clinic_required=True,
        )
        easier_score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=easier,
            score=75,
            max_score=100,
            passed=False,
            clinic_required=True,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{easier.id}/",
            {
                "cutline_mode": "PERCENT",
                "cutline_value": 70,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["effective_cutline_value"], 70)
        self.assertFalse(response.data["uses_session_cutline_default"])
        easier_score.refresh_from_db()
        inherited_score.refresh_from_db()
        self.assertTrue(easier_score.passed)
        self.assertFalse(inherited_score.passed)

        inherited_response = self.client.get(
            f"/api/v1/homeworks/{inherited.id}/",
            **self.req_headers,
        )
        self.assertEqual(inherited_response.status_code, 200, inherited_response.data)
        self.assertEqual(inherited_response.data["effective_cutline_value"], 80)
        self.assertTrue(inherited_response.data["uses_session_cutline_default"])

    def test_bulk_creation_contract_persists_each_homework_cutline(self):
        first = self.client.post(
            "/api/v1/homeworks/",
            {
                "session_id": self.session.id,
                "title": "연산 복습",
                "max_score": 20,
                "cutline_mode": "COUNT",
                "cutline_value": 15,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )
        second = self.client.post(
            "/api/v1/homeworks/",
            {
                "session_id": self.session.id,
                "title": "심화 서술형",
                "max_score": 30,
                "cutline_mode": "COUNT",
                "cutline_value": 24,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["effective_cutline_value"], 15)
        self.assertEqual(second.data["effective_cutline_value"], 24)
        self.assertFalse(first.data["uses_session_cutline_default"])
        self.assertFalse(second.data["uses_session_cutline_default"])

    def test_completion_homework_creation_normalizes_binary_contract(self):
        response = self.client.post(
            "/api/v1/homeworks/",
            {
                "session_id": self.session.id,
                "title": "교재 지참 확인",
                "grading_mode": "COMPLETION",
                "max_score": 30,
                "cutline_mode": "PERCENT",
                "cutline_value": 80,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["grading_mode"], "COMPLETION")
        self.assertEqual(response.data["max_score"], 1.0)
        self.assertEqual(response.data["cutline_mode"], "COUNT")
        self.assertEqual(response.data["cutline_value"], 1)

    def test_grading_mode_change_rejects_existing_results(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="기존 점수 보호 과제",
        )
        HomeworkScore.objects.create(
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
            score=25,
            max_score=30,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {"grading_mode": "COMPLETION"},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("기존 결과", str(response.data["grading_mode"]))
        homework.refresh_from_db()
        self.assertEqual(homework.grading_mode, Homework.GradingMode.SCORE)

    def test_percent_override_is_not_limited_by_session_count_cutline(self):
        policy_res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        policy_id = policy_res.data["results"][0]["id"]
        policy_patch = self.client.patch(
            f"/api/v1/homework/policies/{policy_id}/",
            {"cutline_mode": "COUNT", "cutline_value": 90},
            format="json",
            **self.req_headers,
        )
        self.assertEqual(policy_patch.status_code, 200, policy_patch.data)

        response = self.client.post(
            "/api/v1/homeworks/",
            {
                "session_id": self.session.id,
                "title": "50점 퍼센트 과제",
                "max_score": 50,
                "cutline_mode": "PERCENT",
                "cutline_value": 80,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["max_score"], 50.0)
        self.assertEqual(response.data["effective_cutline_mode"], "PERCENT")

    def test_switching_to_percent_can_lower_max_score_in_same_patch(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="기준 전환 과제",
            meta={"default_max_score": 100},
            cutline_mode=Homework.CutlineMode.COUNT,
            cutline_value=90,
            round_unit_percent=5,
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {
                "max_score": 50,
                "cutline_mode": "PERCENT",
                "cutline_value": 80,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        homework.refresh_from_db()
        self.assertEqual(homework.default_max_score, 50.0)
        self.assertEqual(homework.cutline_mode, Homework.CutlineMode.PERCENT)

    def test_session_policy_change_does_not_overwrite_homework_cutline(self):
        policy_res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        policy_id = policy_res.data["results"][0]["id"]
        overridden = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="70점 기준",
            cutline_mode=Homework.CutlineMode.PERCENT,
            cutline_value=70,
            round_unit_percent=5,
        )
        inherited = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="회차 기본 기준",
        )
        overridden_score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=overridden,
            score=75,
            max_score=100,
            passed=False,
            clinic_required=True,
        )
        inherited_score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=inherited,
            score=75,
            max_score=100,
            passed=True,
            clinic_required=False,
        )

        response = self.client.patch(
            f"/api/v1/homework/policies/{policy_id}/",
            {"cutline_mode": "PERCENT", "cutline_value": 90},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        overridden_score.refresh_from_db()
        inherited_score.refresh_from_db()
        overridden.refresh_from_db()
        self.assertTrue(overridden_score.passed)
        self.assertFalse(inherited_score.passed)
        self.assertEqual(overridden.cutline_value, 70)

    def test_homework_count_cutline_cannot_exceed_its_max_score(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="20점 과제",
            meta={"default_max_score": 20},
        )

        response = self.client.patch(
            f"/api/v1/homeworks/{homework.id}/",
            {
                "cutline_mode": "COUNT",
                "cutline_value": 21,
                "round_unit_percent": 5,
            },
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("cutline_value", response.data)
        homework.refresh_from_db()
        self.assertIsNone(homework.cutline_mode)
        self.assertIsNone(homework.cutline_value)

    def test_assignment_removal_resolves_homework_clinic_link(self):
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Assigned HW",
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            session=self.session,
            homework=homework,
            enrollment=self.enrollment,
        )
        ClinicLink = apps.get_model("progress", "ClinicLink")
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="homework",
            source_id=homework.id,
            meta={"kind": "HOMEWORK_FAILED", "homework_id": homework.id},
        )

        res = self.client.put(
            f"/api/v1/homework/assignments/?homework_id={homework.id}",
            {"enrollment_ids": []},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["removed_assignment_count"], 1)
        self.assertEqual(res.data["removed_clinic_link_count"], 1)

        link.refresh_from_db()
        self.assertIsNotNone(link.resolved_at)
        self.assertEqual(link.resolution_type, ClinicLink.ResolutionType.SOURCE_REMOVED)
        self.assertEqual(link.resolution_evidence["reason"], "homework_assignment_removed")

    def test_assignment_update_preserves_inactive_student_history(self):
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        inactive_user = get_user_model().objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_inactive_history",
            is_active=True,
        )
        inactive_student = Student.objects.create(
            tenant=self.tenant,
            user=inactive_user,
            name="퇴원 이력 학생",
            ps_number=f"PS-INACTIVE-{inactive_user.id}",
            omr_code=f"{inactive_user.id:08d}"[-8:],
        )
        inactive_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=inactive_student,
            lecture=self.lecture,
            status="INACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=inactive_enrollment,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="이력 보존 과제",
        )
        HomeworkAssignment.objects.bulk_create([
            HomeworkAssignment(
                tenant=self.tenant,
                session=self.session,
                homework=homework,
                enrollment=self.enrollment,
            ),
            HomeworkAssignment(
                tenant=self.tenant,
                session=self.session,
                homework=homework,
                enrollment=inactive_enrollment,
            ),
        ])

        response = self.client.put(
            f"/api/v1/homework/assignments/?homework_id={homework.id}",
            {"enrollment_ids": [self.enrollment.id]},
            format="json",
            **self.req_headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            HomeworkAssignment.objects.filter(
                homework=homework,
                enrollment=inactive_enrollment,
            ).exists()
        )
