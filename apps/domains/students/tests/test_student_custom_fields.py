from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.application.services.excel_parsing_service import parse_student_excel_file
from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student, StudentCustomFieldDefinition
from apps.domains.students.services import resolve_student_import_row
from apps.domains.students.views import (
    StudentCustomFieldDefinitionViewSet,
    StudentViewSet,
)


User = get_user_model()


def _tenant(code: str) -> Tenant:
    return Tenant.objects.create(name=f"Tenant {code}", code=code, is_active=True)


def _admin(tenant: Tenant, username: str):
    user = User.objects.create_user(
        username=username,
        password="test1234",
        tenant=tenant,
        is_staff=True,
        name=username,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
    return user


def _student(tenant: Tenant, *, ps_number: str, custom_fields=None) -> Student:
    user = User.objects.create_user(
        username=user_internal_username(tenant, ps_number),
        password="test1234",
        tenant=tenant,
        phone="01011112222",
        name="학생",
    )
    student = Student.objects.create(
        tenant=tenant,
        user=user,
        ps_number=ps_number,
        omr_code="11112222",
        name="학생",
        phone="01011112222",
        parent_phone="01033334444",
        custom_fields=custom_fields or {},
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return student


class StudentCustomFieldTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = _tenant("custom-fields")
        self.other_tenant = _tenant("custom-fields-other")
        self.admin = _admin(self.tenant, "custom-field-admin")

    def _definition_request(self, method: str, path: str, data=None, pk=None):
        request = getattr(self.factory, method)(path, data=data, format="json")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        action = {
            "post": {"post": "create"},
            "get": {"get": "list"},
            "patch": {"patch": "partial_update"},
            "delete": {"delete": "destroy"},
        }[method]
        view = StudentCustomFieldDefinitionViewSet.as_view(action)
        return view(request, **({"pk": pk} if pk is not None else {}))

    def test_definition_key_is_stable_and_old_label_becomes_excel_alias(self):
        created = self._definition_request(
            "post",
            "/api/v1/students/custom-fields/",
            {
                "label": "목표대학",
                "field_type": "text",
                "aliases": ["희망대학"],
                "position": 1,
            },
        )
        self.assertEqual(created.status_code, 201)
        key = created.data["key"]

        updated = self._definition_request(
            "patch",
            f"/api/v1/students/custom-fields/{created.data['id']}/",
            {"label": "목표 대학"},
            pk=created.data["id"],
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["key"], key)
        self.assertIn("목표대학", updated.data["aliases"])
        self.assertIn("희망대학", updated.data["aliases"])

    def test_definition_queryset_and_detail_are_tenant_isolated(self):
        foreign = StudentCustomFieldDefinition.objects.create(
            tenant=self.other_tenant,
            label="타학원컬럼",
        )
        listed = self._definition_request(
            "get",
            "/api/v1/students/custom-fields/",
        )
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(foreign.id, [item["id"] for item in listed.data])

        request = self.factory.patch(
            f"/api/v1/students/custom-fields/{foreign.id}/",
            {"label": "침범"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        response = StudentCustomFieldDefinitionViewSet.as_view(
            {"patch": "partial_update"}
        )(request, pk=foreign.id)
        self.assertEqual(response.status_code, 404)

    def test_core_excel_headers_cannot_be_shadowed(self):
        response = self._definition_request(
            "post",
            "/api/v1/students/custom-fields/",
            {
                "label": "취미",
                "field_type": "text",
                "aliases": ["학부모 전화"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_student_patch_merges_active_values_and_deactivation_preserves_data(self):
        mbti = StudentCustomFieldDefinition.objects.create(
            tenant=self.tenant,
            label="MBTI",
            field_type=StudentCustomFieldDefinition.SELECT,
            options=["INTJ", "ENFP"],
        )
        hobby = StudentCustomFieldDefinition.objects.create(
            tenant=self.tenant,
            label="취미",
        )
        student = _student(
            self.tenant,
            ps_number="CUSTOM-001",
            custom_fields={mbti.key: "INTJ", hobby.key: "독서"},
        )

        request = self.factory.patch(
            f"/api/v1/students/{student.id}/",
            {"custom_fields": {hobby.key: "수영"}},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        response = StudentViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=student.id,
        )
        self.assertEqual(response.status_code, 200)
        student.refresh_from_db()
        self.assertEqual(student.custom_fields[mbti.key], "INTJ")
        self.assertEqual(student.custom_fields[hobby.key], "수영")

        deleted = self._definition_request(
            "delete",
            f"/api/v1/students/custom-fields/{hobby.id}/",
            pk=hobby.id,
        )
        self.assertEqual(deleted.status_code, 204)
        hobby.refresh_from_db()
        student.refresh_from_db()
        self.assertFalse(hobby.is_active)
        self.assertEqual(student.custom_fields[hobby.key], "수영")

    def test_student_rejects_foreign_or_inactive_definition_keys(self):
        foreign = StudentCustomFieldDefinition.objects.create(
            tenant=self.other_tenant,
            label="타학원",
        )
        student = _student(self.tenant, ps_number="CUSTOM-002")
        request = self.factory.patch(
            f"/api/v1/students/{student.id}/",
            {"custom_fields": {foreign.key: "침범"}},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        response = StudentViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=student.id,
        )
        self.assertEqual(response.status_code, 400)
        student.refresh_from_db()
        self.assertEqual(student.custom_fields, {})

    def test_excel_import_maps_labels_and_aliases_without_changing_core_columns(self):
        mbti = StudentCustomFieldDefinition.objects.create(
            tenant=self.tenant,
            label="MBTI",
            field_type=StudentCustomFieldDefinition.SELECT,
            options=["INTJ", "ENFP"],
        )
        average = StudentCustomFieldDefinition.objects.create(
            tenant=self.tenant,
            label="평균등급",
            field_type=StudentCustomFieldDefinition.NUMBER,
            aliases=["작년 평균등급"],
        )
        path = Path(self.id().replace(".", "_") + ".xlsx")
        try:
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(
                ["이름", "학부모전화번호", "학교", "학년", "MBTI", "작년 평균등급", "미사용"]
            )
            sheet.append(
                ["엑셀학생", "010-5555-6666", "검증고", 2, "INTJ", 2.75, "보존안함"]
            )
            workbook.save(path)

            rows, _ = parse_student_excel_file(str(path))
            self.assertEqual(rows[0]["name"], "엑셀학생")
            self.assertEqual(rows[0]["parent_phone"], "01055556666")
            self.assertEqual(rows[0]["_extra_columns"]["MBTI"], "INTJ")
            self.assertEqual(rows[0]["_extra_columns"]["작년 평균등급"], "2.75")

            result = resolve_student_import_row(
                self.tenant,
                rows[0],
                "test1234",
            )
            self.assertTrue(result.created)
            self.assertEqual(result.student.name, "엑셀학생")
            self.assertEqual(result.student.grade, 2)
            self.assertEqual(result.student.high_school, "검증고")
            self.assertEqual(
                result.student.custom_fields,
                {mbti.key: "INTJ", average.key: 2.75},
            )
        finally:
            if path.exists():
                path.unlink()
