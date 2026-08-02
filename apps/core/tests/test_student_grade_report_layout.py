from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.services.student_grade_report_layout import (
    STUDENT_GRADE_REPORT_LAYOUT_KEY,
    STUDENT_GRADE_REPORT_SECTION_IDS,
)


User = get_user_model()


@override_settings(OWNER_TENANT_ID=999_999)
class StudentGradeReportLayoutViewTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="grade-report-layout",
            name="Grade Report Layout",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.ui_config = {"theme": "keep-me"}
        self.program.save(update_fields=["ui_config"])
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }
        self.url = "/api/v1/core/student-grade-report-layout/"

    def _authenticate(self, role: str):
        user = User.objects.create_user(
            username=f"grade-report-{role}",
            password="test1234!",
            tenant=self.tenant,
            is_staff=role != "student",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        self.client.force_authenticate(user=user)
        return user

    @staticmethod
    def _layout(*, hidden: set[str] | None = None, reverse: bool = False):
        ids = list(STUDENT_GRADE_REPORT_SECTION_IDS)
        if reverse:
            ids.reverse()
        hidden = hidden or set()
        return {
            "version": 1,
            "sections": [
                {"id": section_id, "visible": section_id not in hidden}
                for section_id in ids
            ],
        }

    def test_owner_reads_complete_default_layout(self):
        self._authenticate("owner")

        response = self.client.get(self.url, **self.headers)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [section["id"] for section in response.data["sections"]],
            list(STUDENT_GRADE_REPORT_SECTION_IDS),
        )
        self.assertTrue(all(section["visible"] for section in response.data["sections"]))

    def test_admin_reorders_and_hides_sections_without_dropping_other_ui_config(self):
        self._authenticate("admin")
        payload = self._layout(hidden={"improvement_priority"}, reverse=True)

        response = self.client.patch(self.url, payload, format="json", **self.headers)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, payload)
        self.program.refresh_from_db()
        self.assertEqual(self.program.ui_config["theme"], "keep-me")
        self.assertEqual(
            self.program.ui_config[STUDENT_GRADE_REPORT_LAYOUT_KEY],
            payload,
        )

    def test_teacher_cannot_change_tenant_wide_layout(self):
        self._authenticate("teacher")

        response = self.client.patch(
            self.url,
            self._layout(),
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 403)
        self.program.refresh_from_db()
        self.assertNotIn(STUDENT_GRADE_REPORT_LAYOUT_KEY, self.program.ui_config)

    def test_layout_requires_every_section_and_at_least_one_visible(self):
        self._authenticate("owner")

        missing = self._layout()
        missing["sections"].pop()
        missing_response = self.client.patch(
            self.url,
            missing,
            format="json",
            **self.headers,
        )
        hidden_response = self.client.patch(
            self.url,
            self._layout(hidden=set(STUDENT_GRADE_REPORT_SECTION_IDS)),
            format="json",
            **self.headers,
        )

        self.assertEqual(missing_response.status_code, 400)
        self.assertEqual(hidden_response.status_code, 400)

    def test_unknown_stored_sections_are_ignored_and_new_defaults_are_appended(self):
        self._authenticate("owner")
        self.program.ui_config = {
            "theme": "keep-me",
            STUDENT_GRADE_REPORT_LAYOUT_KEY: {
                "version": 0,
                "sections": [
                    {"id": "score_comparison", "visible": False},
                    {"id": "removed_section", "visible": False},
                ],
            },
        }
        self.program.save(update_fields=["ui_config"])

        response = self.client.get(self.url, **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sections"][0], {
            "id": "score_comparison",
            "visible": False,
        })
        self.assertEqual(len(response.data["sections"]), len(STUDENT_GRADE_REPORT_SECTION_IDS))
        self.assertNotIn("removed_section", [row["id"] for row in response.data["sections"]])
