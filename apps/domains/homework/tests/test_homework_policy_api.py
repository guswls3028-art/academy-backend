from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
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

    def test_patch_policy_recalculates_existing_homework_scores(self):
        # ensure policy exists
        res = self.client.get(
            f"/api/v1/homework/policies/?session={self.session.id}",
            **self.req_headers,
        )
        pid = res.data["results"][0]["id"]

        hw = Homework.objects.create(session=self.session, title="HW1")
        hs = HomeworkScore.objects.create(
            enrollment_id=123,
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

