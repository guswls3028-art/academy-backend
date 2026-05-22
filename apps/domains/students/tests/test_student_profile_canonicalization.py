# PATH: apps/domains/students/tests/test_student_profile_canonicalization.py
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.permissions import IsStudent
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.students.selectors import students_for_tenant
from apps.domains.students.views import StudentViewSet
from apps.domains.student_app.profile.views import StudentProfileView

User = get_user_model()


def make_tenant(code="canon"):
    return Tenant.objects.create(name=f"Tenant {code}", code=code, is_active=True)


def make_admin(tenant):
    user = User.objects.create_user(
        username=f"admin-{tenant.code}",
        password="test1234",
        tenant=tenant,
        is_staff=True,
        name="관리자",
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
    return user


def make_student(tenant, *, ps_number="S10001", phone=None, parent_phone="01011112222"):
    user = User.objects.create_user(
        username=user_internal_username(tenant, ps_number),
        password="test1234",
        tenant=tenant,
        phone=phone or "",
        name="학생",
    )
    student = Student.objects.create(
        tenant=tenant,
        user=user,
        ps_number=ps_number,
        name="학생",
        phone=phone,
        parent_phone=parent_phone,
        omr_code=(phone or parent_phone)[-8:],
        school_type="HIGH",
        grade=1,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return student


class StudentSelectorTenantGuardTests(TestCase):
    def test_students_for_tenant_requires_tenant(self):
        with self.assertRaises(ValueError):
            students_for_tenant(None)


class StudentProfileCanonicalizationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = make_tenant()
        self.admin = make_admin(self.tenant)
        self.student = make_student(self.tenant)

    def test_admin_partial_update_relinks_parent_and_recomputes_parent_omr(self):
        request = self.factory.patch(
            f"/api/v1/students/{self.student.id}/",
            data={"parent_phone": "01033334444"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"patch": "partial_update"})(request, pk=self.student.id)

        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.parent_phone, "01033334444")
        self.assertEqual(self.student.omr_code, "33334444")
        self.assertIsNotNone(self.student.parent_id)
        self.assertTrue(
            Parent.objects.filter(
                tenant=self.tenant,
                phone="01033334444",
                students=self.student,
            ).exists()
        )

    def test_student_app_profile_update_uses_same_parent_and_omr_path(self):
        request = self.factory.patch(
            "/api/v1/student/me/",
            data={"parent_phone": "01055556666"},
            format="json",
        )
        force_authenticate(request, user=self.student.user)
        request.tenant = self.tenant

        response = StudentProfileView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.parent_phone, "01055556666")
        self.assertEqual(self.student.omr_code, "55556666")
        self.assertIsNotNone(self.student.parent_id)
        self.assertTrue(
            Parent.objects.filter(
                tenant=self.tenant,
                phone="01055556666",
                students=self.student,
            ).exists()
        )

    def test_students_me_update_uses_same_parent_and_omr_path(self):
        request = self.factory.patch(
            "/api/v1/students/me/",
            data={"parent_phone": "01077778888"},
            format="json",
        )
        force_authenticate(request, user=self.student.user)
        request.tenant = self.tenant

        response = StudentViewSet.as_view(
            {"patch": "me"},
            permission_classes=[IsAuthenticated, IsStudent],
        )(request)

        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.parent_phone, "01077778888")
        self.assertEqual(self.student.omr_code, "77778888")
        self.assertIsNotNone(self.student.parent_id)
