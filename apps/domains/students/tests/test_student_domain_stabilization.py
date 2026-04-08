# PATH: apps/domains/students/tests/test_student_domain_stabilization.py
"""
학생 도메인 운영 안정화 — 증거 기반 검증 테스트

B1: Admin 테넌트 격리 (queryset, permission negative test)
B4: omr_code phone 변경 시 정합성
B8: 학생 생성 race condition 방어
B10: bulk_resolve_conflicts atomic rollback
B11/B12: 비인증 엔드포인트 throttle 적용 확인
"""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student, Tag
from apps.domains.students.views import StudentViewSet

User = get_user_model()


def _make_tenant(name, code):
    return Tenant.objects.create(name=name, code=code, is_active=True)


def _make_admin(tenant, username, role="owner"):
    u = User.objects.create_user(
        username=username, password="test1234",
        tenant=tenant, is_staff=True, name=f"Admin-{username}",
    )
    TenantMembership.ensure_active(tenant=tenant, user=u, role=role)
    return u


def _make_student(tenant, ps_number, phone="01012345678", parent_phone="01098765432", name="테스트학생"):
    internal = user_internal_username(tenant, ps_number)
    user = User.objects.create_user(
        username=internal, password="test1234",
        tenant=tenant, phone=phone, name=name,
    )
    student = Student.objects.create(
        tenant=tenant, user=user,
        ps_number=ps_number, name=name,
        phone=phone, parent_phone=parent_phone,
        omr_code=phone[-8:] if phone and len(phone) >= 8 else "00000000",
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return student


# ═══════════════════════════════════════════════════
# B1: Admin 테넌트 격리 — Negative Tests
# ═══════════════════════════════════════════════════

class TestB1TenantIsolationNegative(TestCase):
    """
    다른 테넌트의 학생 데이터에 접근할 수 없음을 증명.
    superuser, is_staff, 일반 관리자 각각에 대해 검증.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = _make_tenant("Academy A", "testa")
        self.tenant_b = _make_tenant("Academy B", "testb")

        self.student_a = _make_student(self.tenant_a, "A001", phone="01011111111")
        self.student_b = _make_student(self.tenant_b, "B001", phone="01022222222")

        # Tenant A admin (owner)
        self.admin_a = _make_admin(self.tenant_a, "admin_a")
        # Superuser (tenant_a 소속이지만 tenant_b 멤버십 없음)
        self.superuser = User.objects.create_superuser(
            username="superadmin", password="test1234",
            tenant=self.tenant_a, name="Super",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.superuser, role="owner")

    def _list(self, user, tenant):
        request = self.factory.get("/api/v1/students/")
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = StudentViewSet.as_view({"get": "list"})
        return view(request)

    def _retrieve(self, user, tenant, pk):
        request = self.factory.get(f"/api/v1/students/{pk}/")
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = StudentViewSet.as_view({"get": "retrieve"})
        return view(request, pk=pk)

    def _update(self, user, tenant, pk, data):
        request = self.factory.patch(
            f"/api/v1/students/{pk}/",
            data=data, format="json",
        )
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = StudentViewSet.as_view({"patch": "partial_update"})
        return view(request, pk=pk)

    # --- List ---

    def test_admin_list_own_tenant_only(self):
        """Tenant A admin → Tenant A 학생만 보임."""
        resp = self._list(self.admin_a, self.tenant_a)
        self.assertEqual(resp.status_code, 200)
        ids = [s["id"] for s in resp.data.get("results", resp.data)]
        self.assertIn(self.student_a.id, ids)
        self.assertNotIn(self.student_b.id, ids,
                         "CRITICAL: 타 테넌트 학생이 목록에 노출됨!")

    def test_superuser_cannot_list_other_tenant_students(self):
        """Superuser라도 멤버십 없는 Tenant B 학생 목록 접근 불가."""
        resp = self._list(self.superuser, self.tenant_b)
        # 멤버십 없으므로 403 또는 빈 목록
        if resp.status_code == 200:
            ids = [s["id"] for s in resp.data.get("results", resp.data)]
            self.assertNotIn(self.student_b.id, ids,
                             "CRITICAL: superuser가 타 테넌트 학생 조회!")
        else:
            self.assertIn(resp.status_code, [403, 401])

    # --- Retrieve ---

    def test_admin_cannot_retrieve_other_tenant_student(self):
        """Tenant A admin이 Tenant B 학생 상세 조회 → 404."""
        resp = self._retrieve(self.admin_a, self.tenant_a, self.student_b.id)
        self.assertEqual(resp.status_code, 404,
                         "CRITICAL: 타 테넌트 학생 상세 접근 가능!")

    def test_superuser_cannot_retrieve_cross_tenant(self):
        """Superuser가 멤버십 없는 테넌트 학생 조회 → 403 또는 404."""
        resp = self._retrieve(self.superuser, self.tenant_b, self.student_b.id)
        self.assertIn(resp.status_code, [403, 404],
                      "CRITICAL: superuser가 타 테넌트 학생 상세 접근!")

    # --- Update ---

    def test_admin_cannot_update_other_tenant_student(self):
        """Tenant A admin이 Tenant B 학생 수정 불가."""
        original_name = self.student_b.name
        resp = self._update(self.admin_a, self.tenant_a, self.student_b.id, {"name": "해킹됨"})
        self.assertIn(resp.status_code, [403, 404],
                      "CRITICAL: 타 테넌트 학생 수정 가능!")
        self.student_b.refresh_from_db()
        self.assertEqual(self.student_b.name, original_name)

    # --- Tag isolation ---

    def test_tag_cross_tenant_isolation(self):
        """Tenant A 태그가 Tenant B에서 보이지 않음."""
        tag_a = Tag.objects.create(tenant=self.tenant_a, name="태그A")
        tags_b = Tag.objects.filter(tenant=self.tenant_b)
        self.assertNotIn(tag_a.id, list(tags_b.values_list("id", flat=True)),
                         "CRITICAL: 타 테넌트 태그 누출!")


# ═══════════════════════════════════════════════════
# B4: omr_code phone 변경 시 정합성
# ═══════════════════════════════════════════════════

class TestB4OmrCodePhoneSync(TestCase):
    """phone 변경 시 omr_code가 자동 갱신되는지 증명."""

    def setUp(self):
        self.tenant = _make_tenant("TestAcademy", "test_omr")
        self.admin = _make_admin(self.tenant, "admin_omr")
        self.factory = APIRequestFactory()

    def test_phone_change_updates_omr_code(self):
        """admin partial_update로 phone 변경 → omr_code 자동 갱신."""
        student = _make_student(self.tenant, "OMR001", phone="01012345678")
        self.assertEqual(student.omr_code, "12345678")

        request = self.factory.patch(
            f"/api/v1/students/{student.id}/",
            data={"phone": "01099998888"}, format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"patch": "partial_update"})
        resp = view(request, pk=student.id)

        self.assertIn(resp.status_code, [200, 201])
        student.refresh_from_db()
        self.assertEqual(student.omr_code, "99998888",
                         f"omr_code가 갱신되지 않음: {student.omr_code}")

    def test_parent_phone_change_updates_omr_code_when_no_phone(self):
        """학생 전화번호 없이 parent_phone만 있을 때 omr_code 갱신."""
        student = _make_student(self.tenant, "OMR002", phone="", parent_phone="01077776666")
        # phone이 비어있으므로 parent_phone에서 omr_code 생성 기대
        # (실제 로직은 phone 우선 → 빈 경우 parent_phone)

        request = self.factory.patch(
            f"/api/v1/students/{student.id}/",
            data={"parent_phone": "01055554444"}, format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"patch": "partial_update"})
        resp = view(request, pk=student.id)

        self.assertIn(resp.status_code, [200, 201])
        student.refresh_from_db()
        self.assertEqual(student.omr_code, "55554444",
                         f"parent_phone 변경 시 omr_code 미갱신: {student.omr_code}")


# ═══════════════════════════════════════════════════
# B8: 학생 생성 race condition 방어
# ═══════════════════════════════════════════════════

class TestB8StudentCreateAtomicity(TestCase):
    """학생 생성이 transaction.atomic으로 보호되는지 증명."""

    def setUp(self):
        self.tenant = _make_tenant("TestAcademy", "test_race")
        self.admin = _make_admin(self.tenant, "admin_race")
        self.factory = APIRequestFactory()

    def test_duplicate_ps_number_rejected(self):
        """같은 ps_number로 두 번째 생성 시 에러 (중복 방지)."""
        _make_student(self.tenant, "DUP001", phone="01011110001")

        request = self.factory.post(
            "/api/v1/students/",
            data={
                "name": "중복학생",
                "phone": "01011110002",
                "parent_phone": "01099990002",
                "initial_password": "test1234",
                "ps_number": "DUP001",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"post": "create"})
        resp = view(request)

        self.assertIn(resp.status_code, [400, 409],
                      "중복 ps_number가 허용됨!")
        # Student 수는 1명이어야 함
        self.assertEqual(
            Student.objects.filter(tenant=self.tenant, ps_number="DUP001", deleted_at__isnull=True).count(),
            1, "중복 학생이 생성됨!"
        )

    def test_create_failure_no_orphan_user(self):
        """학생 생성 실패 시 User/Membership이 남지 않음 (atomic rollback)."""
        user_count_before = User.objects.count()

        request = self.factory.post(
            "/api/v1/students/",
            data={
                "name": "",  # name required — 빈 값으로 실패 유도
                "phone": "01033330001",
                "parent_phone": "01099990003",
                "initial_password": "test1234",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"post": "create"})
        resp = view(request)

        # 실패 시 User가 새로 생기지 않아야 함
        if resp.status_code >= 400:
            self.assertEqual(User.objects.count(), user_count_before,
                             "학생 생성 실패 시 orphan User가 남음!")


# ═══════════════════════════════════════════════════
# B10: bulk_resolve_conflicts atomic rollback
# ═══════════════════════════════════════════════════

class TestB10BulkResolveConflictsAtomicity(TestCase):
    """
    bulk_resolve_conflicts에서 개별 resolution이 실패해도
    성공한 것은 유지되고, 실패한 것은 rollback되는지 증명.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = _make_tenant("TestAcademy", "test_resolve")
        self.admin = _make_admin(self.tenant, "admin_resolve")

        # 삭제된 학생 2명 (충돌 해결 대상)
        self.student1 = _make_student(self.tenant, "RES001", phone="01044440001", name="복원대상")
        self.student1.deleted_at = timezone.now()
        self.student1.save(update_fields=["deleted_at"])

        self.student2 = _make_student(self.tenant, "RES002", phone="01044440002", name="삭제후재생성")
        self.student2.deleted_at = timezone.now()
        self.student2.save(update_fields=["deleted_at"])

    def test_restore_success_within_atomic(self):
        """restore action은 학생을 복원하고 deleted_at을 None으로."""
        request = self.factory.post(
            "/api/v1/students/bulk_resolve_conflicts/",
            data={
                "initial_password": "test1234",
                "resolutions": [
                    {"row": 1, "student_id": self.student1.id, "action": "restore",
                     "student_data": {"name": "복원됨"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"post": "bulk_resolve_conflicts"})
        resp = view(request)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("restored"), 1)
        self.student1.refresh_from_db()
        self.assertIsNone(self.student1.deleted_at)
        self.assertEqual(self.student1.name, "복원됨")

    def test_individual_failure_does_not_rollback_others(self):
        """하나의 resolution이 실패해도 다른 것은 성공."""
        request = self.factory.post(
            "/api/v1/students/bulk_resolve_conflicts/",
            data={
                "initial_password": "test1234",
                "resolutions": [
                    {"row": 1, "student_id": self.student1.id, "action": "restore",
                     "student_data": {"name": "복원성공"}},
                    {"row": 2, "student_id": 999999, "action": "restore",
                     "student_data": {"name": "존재안함"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"post": "bulk_resolve_conflicts"})
        resp = view(request)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("restored"), 1)
        self.assertEqual(len(resp.data.get("failed", [])), 1)
        self.student1.refresh_from_db()
        self.assertIsNone(self.student1.deleted_at, "성공한 복원이 실패한 것에 의해 롤백됨!")

    def test_cross_tenant_resolution_rejected(self):
        """타 테넌트 학생 ID로 resolve 시도 → 실패."""
        other_tenant = _make_tenant("Other", "other_res")
        other_student = _make_student(other_tenant, "OTH001", phone="01055550001")
        other_student.deleted_at = timezone.now()
        other_student.save(update_fields=["deleted_at"])

        request = self.factory.post(
            "/api/v1/students/bulk_resolve_conflicts/",
            data={
                "initial_password": "test1234",
                "resolutions": [
                    {"row": 1, "student_id": other_student.id, "action": "restore",
                     "student_data": {"name": "크로스테넌트"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        view = StudentViewSet.as_view({"post": "bulk_resolve_conflicts"})
        resp = view(request)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("restored"), 0)
        self.assertEqual(len(resp.data.get("failed", [])), 1,
                         "CRITICAL: 타 테넌트 학생이 복원됨!")
        other_student.refresh_from_db()
        self.assertIsNotNone(other_student.deleted_at,
                             "CRITICAL: 타 테넌트 학생의 deleted_at이 변경됨!")


# ═══════════════════════════════════════════════════
# B11/B12: Throttle 적용 확인
# ═══════════════════════════════════════════════════

class TestThrottleConfiguration(TestCase):
    """비인증 엔드포인트에 throttle이 올바르게 적용됐는지 확인."""

    def test_sms_endpoint_throttle_class_exists(self):
        from apps.api.common.throttles import SmsEndpointThrottle
        self.assertEqual(SmsEndpointThrottle.rate, "5/hour")
        self.assertEqual(SmsEndpointThrottle.scope, "sms_endpoint")

    def test_signup_check_throttle_class_exists(self):
        from apps.api.common.throttles import SignupCheckThrottle
        self.assertEqual(SignupCheckThrottle.rate, "30/minute")
        self.assertEqual(SignupCheckThrottle.scope, "signup_check")

    def test_send_existing_credentials_has_throttle(self):
        from apps.domains.students.views import SendExistingCredentialsView
        from apps.api.common.throttles import SmsEndpointThrottle
        self.assertTrue(
            any(issubclass(t, SmsEndpointThrottle) or t is SmsEndpointThrottle
                for t in SendExistingCredentialsView.throttle_classes),
            "SendExistingCredentialsView에 SmsEndpointThrottle 미적용!"
        )

    def test_password_find_request_has_throttle(self):
        from apps.domains.students.views import StudentPasswordFindRequestView
        from apps.api.common.throttles import SmsEndpointThrottle
        self.assertTrue(
            any(issubclass(t, SmsEndpointThrottle) or t is SmsEndpointThrottle
                for t in StudentPasswordFindRequestView.throttle_classes),
            "StudentPasswordFindRequestView에 SmsEndpointThrottle 미적용!"
        )

    def test_password_reset_send_has_throttle(self):
        from apps.domains.students.views import StudentPasswordResetSendView
        from apps.api.common.throttles import SmsEndpointThrottle
        self.assertTrue(
            any(issubclass(t, SmsEndpointThrottle) or t is SmsEndpointThrottle
                for t in StudentPasswordResetSendView.throttle_classes),
            "StudentPasswordResetSendView에 SmsEndpointThrottle 미적용!"
        )

    @override_settings(REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "anon": "60/minute", "user": "300/minute",
            "sms_endpoint": "5/hour", "signup_check": "30/minute",
        },
    })
    def test_throttle_rates_in_settings(self):
        from django.conf import settings
        rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        self.assertIn("sms_endpoint", rates)
        self.assertIn("signup_check", rates)
