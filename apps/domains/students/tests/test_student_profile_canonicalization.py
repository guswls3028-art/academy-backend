# PATH: apps/domains/students/tests/test_student_profile_canonicalization.py
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.permissions import IsStudent
from apps.domains.students.models import Student
from apps.domains.students.selectors import students_for_tenant
from apps.domains.students.views import StudentViewSet
from apps.domains.student_app.profile.views import StudentProfileView
from apps.support.students.lifecycle_dependencies import ensure_parent_account_for_student

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
        self.assertEqual(self.student.parent.phone, "01033334444")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_admin_add_student_phone_sends_student_account_notice(self, send_mock):
        no_phone_student = make_student(self.tenant, ps_number="S-NOPHONE", phone=None)
        request = self.factory.patch(
            f"/api/v1/students/{no_phone_student.id}/",
            data={"phone": "01099998888"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"patch": "partial_update"})(request, pk=no_phone_student.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "registration_approved_student")
        self.assertEqual(send_mock.call_args.kwargs["to"], "01099998888")
        self.assertEqual(
            send_mock.call_args.kwargs["replacements"]["학생비밀번호"],
            "변경되지 않음",
        )

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_admin_student_id_change_sends_student_account_notice(self, send_mock):
        request = self.factory.patch(
            f"/api/v1/students/{self.student.id}/",
            data={"ps_number": "S-CHANGED"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"patch": "partial_update"})(request, pk=self.student.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "registration_approved_student")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생아이디"], "S-CHANGED")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_admin_parent_phone_change_sends_parent_account_notice(self, send_mock):
        request = self.factory.patch(
            f"/api/v1/students/{self.student.id}/",
            data={"parent_phone": "01022223333"},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"patch": "partial_update"})(request, pk=self.student.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "registration_approved_parent")
        self.assertEqual(send_mock.call_args.kwargs["to"], "01022223333")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학부모비밀번호"], "3333")

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
        self.assertEqual(self.student.parent.phone, "01055556666")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_student_app_password_change_sends_student_notice(self, send_mock):
        request = self.factory.patch(
            "/api/v1/student-app/me/",
            data={"current_password": "test1234", "new_password": "newpass1234"},
            format="json",
        )
        force_authenticate(request, user=self.student.user)
        request.tenant = self.tenant

        response = StudentProfileView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.student.user.refresh_from_db()
        self.assertTrue(self.student.user.check_password("newpass1234"))
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "password_reset_student")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생비밀번호"], "newpass1234")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_student_app_username_change_sends_student_account_notice(self, send_mock):
        request = self.factory.patch(
            "/api/v1/student-app/me/",
            data={"username": "S-SELF-APP"},
            format="json",
        )
        force_authenticate(request, user=self.student.user)
        request.tenant = self.tenant

        response = StudentProfileView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.student.user.refresh_from_db()
        self.assertEqual(self.student.ps_number, "S-SELF-APP")
        self.assertEqual(self.student.user.username, user_internal_username(self.tenant, "S-SELF-APP"))
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "registration_approved_student")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생아이디"], "S-SELF-APP")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생비밀번호"], "변경되지 않음")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_parent_password_change_sends_parent_notice(self, send_mock):
        from apps.core.views.auth import ChangePasswordView

        parent_result = ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01044445555",
            student_name=self.student.name,
        )
        parent = parent_result.parent
        parent_user = parent.user
        parent_user.set_password("parent1234")
        parent_user.save(update_fields=["password"])
        self.student.parent = parent
        self.student.parent_phone = parent.phone
        self.student.save(update_fields=["parent", "parent_phone"])

        request = self.factory.post(
            "/api/v1/core/me/profile/change-password/",
            data={"old_password": "parent1234", "new_password": "parent9999"},
            format="json",
        )
        force_authenticate(request, user=parent_user)
        request.tenant = self.tenant

        response = ChangePasswordView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        parent_user.refresh_from_db()
        self.assertTrue(parent_user.check_password("parent9999"))
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "password_reset_parent")
        self.assertEqual(send_mock.call_args.kwargs["to"], "01044445555")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학부모비밀번호"], "parent9999")

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

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_students_me_username_change_sends_student_account_notice(self, send_mock):
        request = self.factory.patch(
            "/api/v1/students/me/",
            data={"username": "S-SELF-LEGACY"},
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
        self.student.user.refresh_from_db()
        self.assertEqual(self.student.ps_number, "S-SELF-LEGACY")
        self.assertEqual(self.student.user.username, user_internal_username(self.tenant, "S-SELF-LEGACY"))
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "registration_approved_student")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생아이디"], "S-SELF-LEGACY")

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_students_me_password_change_sends_student_notice(self, send_mock):
        request = self.factory.patch(
            "/api/v1/students/me/",
            data={"current_password": "test1234", "new_password": "legacy9999"},
            format="json",
        )
        force_authenticate(request, user=self.student.user)
        request.tenant = self.tenant

        response = StudentViewSet.as_view(
            {"patch": "me"},
            permission_classes=[IsAuthenticated, IsStudent],
        )(request)

        self.assertEqual(response.status_code, 200)
        self.student.user.refresh_from_db()
        self.assertTrue(self.student.user.check_password("legacy9999"))
        self.assertEqual(send_mock.call_args.kwargs["trigger"], "password_reset_student")
        self.assertEqual(send_mock.call_args.kwargs["replacements"]["학생비밀번호"], "legacy9999")
