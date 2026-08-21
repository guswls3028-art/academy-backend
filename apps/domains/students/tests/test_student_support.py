from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    PRODUCT_ANALYTICS_HASH_KEY="student-support-test-key",
)
class StudentSupportTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="학생지원학원", code="support-test")
        User = get_user_model()
        self.student_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S001"),
            password="studentpw123",
            tenant=self.tenant,
            token_version=0,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.student_user,
            role="student",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="S001",
            omr_code="11112222",
            name="학생지원",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.staff = User.objects.create_user(
            username=user_internal_username(self.tenant, "teacher01"),
            password="teacherpw123",
            tenant=self.tenant,
            token_version=0,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.staff,
            role="teacher",
        )

    def _headers(self, user=None):
        target = user or self.staff
        token = AccessToken.for_user(target)
        token["tenant_id"] = self.tenant.id
        token["token_version"] = 0
        return {
            "HTTP_HOST": "api.hakwonplus.com",
            "HTTP_X_TENANT_CODE": self.tenant.code,
            "HTTP_AUTHORIZATION": f"Bearer {str(token)}",
        }

    def _activity(self, *, category: str, support: bool):
        return OpsAuditLog.objects.create(
            actor_user=self.staff if support else self.student_user,
            actor_username=(self.staff if support else self.student_user).username,
            action="student_activity.screen_view",
            summary=f"{category} 화면을 확인했어요",
            target_tenant=self.tenant,
            target_user=self.student_user,
            payload={
                "student_id": self.student.id,
                "actor_mode": "support" if support else "student",
                "category": category,
                "device_class": "mobile",
                "screen_id": f"student.{category}.home",
            },
        )

    def test_real_student_login_is_recorded_once(self):
        response = APIClient().post(
            "/api/v1/token/",
            {
                "username": "S001",
                "password": "studentpw123",
                "tenant_code": self.tenant.code,
            },
            format="json",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
            HTTP_X_CLIENT_VERSION="login-test",
        )

        self.assertEqual(response.status_code, 200, response.content)
        event = OpsAuditLog.objects.get(action="student_activity.login")
        self.assertEqual(event.actor_user, self.student_user)
        self.assertEqual(event.target_user, self.student_user)
        self.assertEqual(event.payload["actor_mode"], "student")

    def test_support_session_is_access_only_and_not_a_student_login(self):
        response = APIClient().post(
            f"/api/v1/students/{self.student.id}/support-session/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        claims = AccessToken(response.data["access"])
        self.assertTrue(claims["support_preview"])
        self.assertEqual(claims["impersonated_by"], self.staff.id)
        self.assertEqual(claims["support_student_id"], self.student.id)
        self.assertFalse(OpsAuditLog.objects.filter(action="student_activity.login").exists())
        self.assertTrue(
            OpsAuditLog.objects.filter(
                action="student_support_view.start",
                actor_user=self.staff,
                target_user=self.student_user,
            ).exists()
        )

    def test_activity_timeline_excludes_support_by_default(self):
        self._activity(category="video", support=False)
        self._activity(category="result", support=True)

        client = APIClient()
        response = client.get(
            f"/api/v1/students/{self.student.id}/activities/?days=30",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["category"], "video")
        self.assertEqual(response.data["results"][0]["actor_mode"], "student")

        response = client.get(
            f"/api/v1/students/{self.student.id}/activities/?days=30&include_support=1",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {item["actor_mode"] for item in response.data["results"]},
            {"student", "support"},
        )

    def test_student_and_support_screen_views_keep_distinct_actors(self):
        student_response = APIClient().post(
            "/api/v1/students/me/activity/",
            {"screen_id": "student.video.player", "device_class": "mobile"},
            format="json",
            **self._headers(self.student_user),
        )
        self.assertEqual(student_response.status_code, 202, student_response.content)

        support_session = APIClient().post(
            f"/api/v1/students/{self.student.id}/support-session/",
            {},
            format="json",
            **self._headers(),
        )
        support_response = APIClient().post(
            "/api/v1/students/me/activity/",
            {"screen_id": "student.exam.result", "device_class": "desktop"},
            format="json",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
            HTTP_AUTHORIZATION=f"Bearer {support_session.data['access']}",
        )
        self.assertEqual(support_response.status_code, 202, support_response.content)

        student_event = OpsAuditLog.objects.get(
            action="student_activity.screen_view",
            payload__actor_mode="student",
        )
        support_event = OpsAuditLog.objects.get(
            action="student_activity.screen_view",
            payload__actor_mode="support",
        )
        self.assertEqual(student_event.actor_user, self.student_user)
        self.assertEqual(student_event.target_user, self.student_user)
        self.assertEqual(support_event.actor_user, self.staff)
        self.assertEqual(support_event.target_user, self.student_user)
        self.assertEqual(support_event.summary, "자기 시험 결과를 확인했어요")

    def test_activity_filter_and_tenant_scope_fail_closed(self):
        self._activity(category="video", support=False)
        self._activity(category="exam", support=False)

        filtered = APIClient().get(
            f"/api/v1/students/{self.student.id}/activities/?days=7&category=exam",
            **self._headers(),
        )
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(filtered.data["count"], 1)
        self.assertEqual(filtered.data["results"][0]["category"], "exam")

        other_tenant = Tenant.objects.create(name="다른학원", code="support-other")
        other_user = get_user_model().objects.create_user(
            username=user_internal_username(other_tenant, "S999"),
            password="otherpw123",
            tenant=other_tenant,
        )
        other_student = Student.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="S999",
            omr_code="99998888",
            name="다른학생",
            parent_phone="01099998888",
        )
        denied = APIClient().post(
            f"/api/v1/students/{other_student.id}/support-session/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(denied.status_code, 404)

    def test_support_token_expires_within_fifteen_minutes(self):
        response = APIClient().post(
            f"/api/v1/students/{self.student.id}/support-session/",
            {},
            format="json",
            **self._headers(),
        )
        claims = AccessToken(response.data["access"])
        self.assertLessEqual(claims["exp"] - claims["iat"], 15 * 60)

    def test_support_token_fails_closed_after_operator_access_is_revoked(self):
        response = APIClient().post(
            f"/api/v1/students/{self.student.id}/support-session/",
            {},
            format="json",
            **self._headers(),
        )
        TenantMembership.objects.filter(
            tenant=self.tenant,
            user=self.staff,
        ).update(is_active=False)

        denied = APIClient().get(
            "/api/v1/student/dashboard/",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
        )

        self.assertEqual(denied.status_code, 401, denied.content)
