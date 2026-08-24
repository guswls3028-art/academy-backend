from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.core.models import Tenant, TenantMembership
from apps.domains.students.models import Student, StudentRegistrationRequest
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.registration_approval import (
    RegistrationApprovalError,
    approve_registration_request,
)
from apps.domains.students.views.registration_views import RegistrationRequestViewSet
from apps.support.students.lifecycle_dependencies import ensure_parent_account_for_student


User = get_user_model()


class RegistrationApprovalIdentityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="가입 식별 학원",
            code="registration-identity",
            is_active=True,
        )

    def _student(
        self,
        *,
        name: str = "기존학생",
        ps_number: str = "EXISTING-001",
        phone: str = "01070001111",
        parent_phone: str = "01070002222",
    ) -> Student:
        return create_student_account(
            tenant=self.tenant,
            password="teacher-password",
            student_data={
                "name": name,
                "ps_number": ps_number,
                "phone": phone,
                "parent_phone": parent_phone,
                "omr_code": (phone or parent_phone)[-8:],
                "uses_identifier": False,
                "school_type": "HIGH",
                "grade": 1,
            },
        ).student

    def _registration(
        self,
        *,
        name: str = "기존학생",
        username: str = "EXISTING-001",
        phone: str | None = "01070001111",
        parent_phone: str = "01070002222",
        tenant: Tenant | None = None,
    ) -> StudentRegistrationRequest:
        return StudentRegistrationRequest.objects.create(
            tenant=tenant or self.tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("signup-password"),
            initial_password_plain="",
            name=name,
            username=username,
            parent_phone=parent_phone,
            phone=phone,
            school_type="HIGH",
            high_school="테스트고",
            origin_middle_school="테스트중",
            grade=1,
            gender="M",
            address="서울",
        )

    def _signup_payload(self) -> dict[str, object]:
        return {
            "name": "가입학생",
            "username": "SIGNUP-001",
            "initial_password": "signup-password",
            "parent_phone": "01071112222",
            "phone": "01073334444",
            "school_type": "HIGH",
            "high_school": "테스트고",
            "origin_middle_school": "테스트중",
            "grade": 1,
            "gender": "M",
            "address": "서울",
        }

    def _parent_count(self, *, tenant: Tenant | None = None) -> int:
        parent_model = Student._meta.get_field("parent").remote_field.model
        queryset = parent_model.objects.all()
        if tenant is not None:
            queryset = queryset.filter(tenant=tenant)
        return queryset.count()

    def test_approval_reuses_exact_existing_student_without_changing_identity_or_credentials(self):
        student = self._student()
        registration = self._registration(username="REQUESTED-NEW-ID")
        student.refresh_from_db()
        student.user.refresh_from_db()
        original = {
            "student_count": Student.objects.filter(tenant=self.tenant).count(),
            "parent_count": self._parent_count(tenant=self.tenant),
            "user_count": User.objects.filter(tenant=self.tenant).count(),
            "membership_count": TenantMembership.objects.filter(tenant=self.tenant).count(),
            "ps_number": student.ps_number,
            "password": student.user.password,
            "token_version": student.user.token_version,
            "student_notice": student.pending_account_notice_student_password_ciphertext,
            "parent_notice": student.pending_account_notice_parent_password_ciphertext,
        }

        result = approve_registration_request(
            tenant=self.tenant,
            registration_id=registration.id,
        )

        registration.refresh_from_db()
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(result.student.id, student.id)
        self.assertEqual(registration.student_id, student.id)
        self.assertEqual(registration.status, StudentRegistrationRequest.APPROVED)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), original["student_count"])
        self.assertEqual(self._parent_count(tenant=self.tenant), original["parent_count"])
        self.assertEqual(User.objects.filter(tenant=self.tenant).count(), original["user_count"])
        self.assertEqual(
            TenantMembership.objects.filter(tenant=self.tenant).count(),
            original["membership_count"],
        )
        self.assertEqual(student.ps_number, original["ps_number"])
        self.assertEqual(student.user.password, original["password"])
        self.assertEqual(student.user.token_version, original["token_version"])
        self.assertEqual(
            student.pending_account_notice_student_password_ciphertext,
            original["student_notice"],
        )
        self.assertEqual(
            student.pending_account_notice_parent_password_ciphertext,
            original["parent_notice"],
        )
        self.assertEqual(result.notice.student_id, original["ps_number"])
        self.assertEqual(result.notice.student_password, "변경되지 않음")
        self.assertEqual(result.notice.parent_password, "변경되지 않음")

    def test_approval_fails_closed_when_phone_matches_multiple_active_students(self):
        self._student(ps_number="AMBIGUOUS-001")
        self._student(ps_number="AMBIGUOUS-002")
        registration = self._registration(username="REQUESTED-AMBIGUOUS")
        original_counts = (Student.objects.count(), User.objects.count(), self._parent_count())

        with self.assertRaisesMessage(RegistrationApprovalError, "여러 명"):
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)
        self.assertEqual(
            (Student.objects.count(), User.objects.count(), self._parent_count()),
            original_counts,
        )

    def test_approval_reuses_parent_phone_identity_when_student_phone_is_empty(self):
        parent_phone = "01070002222"
        student = self._student(phone="", parent_phone=parent_phone)
        registration = self._registration(
            username="REQUESTED-PARENT-PHONE",
            phone=parent_phone,
            parent_phone=parent_phone,
        )
        original_counts = (Student.objects.count(), User.objects.count(), self._parent_count())

        result = approve_registration_request(
            tenant=self.tenant,
            registration_id=registration.id,
        )

        registration.refresh_from_db()
        self.assertEqual(result.student.id, student.id)
        self.assertEqual(registration.student_id, student.id)
        self.assertEqual(
            (Student.objects.count(), User.objects.count(), self._parent_count()),
            original_counts,
        )

    def test_approval_fails_closed_for_matching_deleted_student(self):
        student = self._student()
        student.deleted_at = timezone.now()
        student.save(update_fields=["deleted_at", "updated_at"])
        registration = self._registration()
        original_counts = (Student.objects.count(), User.objects.count(), self._parent_count())

        with self.assertRaisesMessage(RegistrationApprovalError, "삭제"):
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)
        self.assertEqual(
            (Student.objects.count(), User.objects.count(), self._parent_count()),
            original_counts,
        )

    def test_approval_fails_closed_for_cross_tenant_user_link_drift(self):
        other_tenant = Tenant.objects.create(name="다른 학원", code="other-registration", is_active=True)
        foreign_user = User.objects.create_user(
            username="foreign-student-user",
            password="foreign-password",
            tenant=other_tenant,
            phone="01070001111",
        )
        Student.objects.create(
            tenant=self.tenant,
            user=foreign_user,
            name="기존학생",
            ps_number="FOREIGN-LINK",
            phone="01070001111",
            parent_phone="01070002222",
            omr_code="70001111",
            school_type="HIGH",
            grade=1,
        )
        registration = self._registration(username="FOREIGN-LINK")
        original_counts = (Student.objects.count(), User.objects.count(), self._parent_count())

        with self.assertRaisesMessage(RegistrationApprovalError, "테넌트"):
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)
        self.assertEqual(
            (Student.objects.count(), User.objects.count(), self._parent_count()),
            original_counts,
        )

    def test_cross_tenant_phone_match_is_never_reused(self):
        other_tenant = Tenant.objects.create(name="다른 학원", code="other-phone", is_active=True)
        original_tenant = self.tenant
        self.tenant = other_tenant
        other_student = self._student()
        self.tenant = original_tenant
        registration = self._registration(username="LOCAL-STUDENT")

        result = approve_registration_request(
            tenant=self.tenant,
            registration_id=registration.id,
        )

        self.assertNotEqual(result.student.id, other_student.id)
        self.assertEqual(result.student.tenant_id, self.tenant.id)
        self.assertEqual(Student.objects.filter(phone="01070001111").count(), 2)

    def test_existing_student_reuse_rejects_student_username_drift(self):
        student = self._student()
        student.user.username = "drifted-student-login"
        student.user.save(update_fields=["username"])
        registration = self._registration(username="REQUESTED-AFTER-DRIFT")

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)

    def test_existing_student_reuse_rejects_inactive_student_user(self):
        student = self._student()
        student.user.is_active = False
        student.user.save(update_fields=["is_active"])
        registration = self._registration(username="REQUESTED-INACTIVE")

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)

    def test_existing_student_reuse_rejects_missing_student_membership(self):
        student = self._student()
        TenantMembership.objects.filter(tenant=self.tenant, user=student.user).delete()
        registration = self._registration(username="REQUESTED-NO-STUDENT-MEMBERSHIP")

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)

    def test_existing_student_reuse_rejects_missing_parent_user(self):
        student = self._student()
        parent = student.parent
        parent.user = None
        parent.save(update_fields=["user"])
        registration = self._registration(username="REQUESTED-NO-PARENT-USER")

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)

    def test_existing_student_reuse_rejects_missing_parent_membership(self):
        student = self._student()
        TenantMembership.objects.filter(tenant=self.tenant, user=student.parent.user).delete()
        registration = self._registration(username="REQUESTED-NO-PARENT-MEMBERSHIP")

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)

    def test_new_student_rejects_linked_parent_user_phone_and_identity_drift(self):
        parent_result = ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01076662222",
            student_name="신규학생",
            initial_password="parent-password",
        )
        parent_user = parent_result.parent.user
        parent_user.username = "drifted-parent-login"
        parent_user.phone = "01070009999"
        parent_user.save(update_fields=["username", "phone"])
        registration = self._registration(
            name="신규학생",
            username="NEW-STUDENT-AFTER-PARENT-DRIFT",
            phone="01076661111",
            parent_phone="01076662222",
        )

        with self.assertRaises(RegistrationApprovalError) as ctx:
            approve_registration_request(
                tenant=self.tenant,
                registration_id=registration.id,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
        self.assertIsNone(registration.student_id)
        self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())

    def test_new_approval_notice_uses_persisted_shared_phone_identity(self):
        shared_phone = "01074445555"
        registration = self._registration(
            name="공유번호신규",
            username=shared_phone,
            phone=shared_phone,
            parent_phone=shared_phone,
        )

        result = approve_registration_request(
            tenant=self.tenant,
            registration_id=registration.id,
        )

        result.student.refresh_from_db()
        result.student.user.refresh_from_db()
        self.assertIsNone(result.student.phone)
        self.assertEqual(result.student.user.phone, "")
        self.assertTrue(result.student.uses_identifier)
        self.assertNotEqual(result.student.ps_number, shared_phone)
        self.assertEqual(result.notice.student_phone, "")
        self.assertEqual(result.notice.student_id, result.student.ps_number)
        self.assertEqual(result.notice.parent_phone, shared_phone)

    def test_disabled_tenants_reject_public_signup_and_pending_approval(self):
        for code in ("godmin", "tchul"):
            with self.subTest(code=code):
                tenant = Tenant.objects.create(name=code, code=code, is_active=True)
                duplicate_request = self.factory.post(
                    "/api/v1/students/registration_requests/check_duplicate/",
                    {"phone": "01073334444"},
                    format="json",
                )
                duplicate_request.tenant = tenant
                duplicate_response = RegistrationRequestViewSet.as_view(
                    {"post": "check_duplicate"}
                )(duplicate_request)
                self.assertEqual(duplicate_response.status_code, 403)
                self.assertEqual(
                    duplicate_response.data["code"],
                    "self_registration_disabled",
                )

                request = self.factory.post(
                    "/api/v1/students/registration_requests/",
                    self._signup_payload(),
                    format="json",
                )
                request.tenant = tenant

                response = RegistrationRequestViewSet.as_view({"post": "create"})(request)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data["code"], "self_registration_disabled")
                self.assertFalse(StudentRegistrationRequest.objects.filter(tenant=tenant).exists())

                registration = self._registration(
                    tenant=tenant,
                    username=f"{code}-pending",
                    phone="01079990001",
                    parent_phone="01079990002",
                )
                with self.assertRaisesMessage(RegistrationApprovalError, "회원가입"):
                    approve_registration_request(
                        tenant=tenant,
                        registration_id=registration.id,
                    )
                registration.refresh_from_db()
                self.assertEqual(registration.status, StudentRegistrationRequest.PENDING)
                self.assertIsNone(registration.student_id)
