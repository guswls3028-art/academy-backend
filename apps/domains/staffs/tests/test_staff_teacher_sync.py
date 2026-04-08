"""
Staff 도메인 운영 안정화 테스트.
- Staff-Teacher 연동 (이름/전화 변경, 비활성화/재활성화, 동시 변경)
- Staff 삭제 정책 (Owner 삭제 방지, Teacher cascade)
- Staff role 판별
- 비밀번호 변경
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.staffs.models import Staff, WorkType, StaffWorkType
from apps.domains.staffs.serializers import StaffCreateUpdateSerializer, StaffListSerializer
from apps.domains.teachers.models import Teacher
from academy.adapters.db.django import repositories_teachers as teacher_repo

User = get_user_model()


def _make_tenant(name="테스트학원"):
    return Tenant.objects.create(name=name, code=name.lower().replace(" ", ""))


def _make_request(tenant, user=None):
    factory = RequestFactory()
    request = factory.get("/")
    request.tenant = tenant
    if user:
        request.user = user
    return request


def _create_staff_teacher(tenant, name="김강사", phone="01011112222"):
    """Staff(TEACHER role) + Teacher 레코드를 함께 생성."""
    user = User.objects.create_user(
        username=f"t{tenant.id}_{name}",
        password="test1234",
        name=name,
        phone=phone,
        tenant=tenant,
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])

    staff = Staff.objects.create(
        tenant=tenant,
        user=user,
        name=name,
        phone=phone,
    )
    Teacher.objects.create(
        tenant=tenant,
        name=name,
        phone=phone or "",
        is_active=True,
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role="teacher",
        is_active=True,
    )
    return staff


class TestStaffTeacherNameSync(TestCase):
    """이슈 2: Staff 이름 변경 시 Teacher 레코드 동기화."""

    def setUp(self):
        self.tenant = _make_tenant()
        self.staff = _create_staff_teacher(self.tenant, name="김강사", phone="01011112222")

    def test_name_change_syncs_teacher(self):
        """Staff 이름 변경 → Teacher.name도 변경됨."""
        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"name": "김선생"},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        teacher = Teacher.objects.get(tenant=self.tenant, phone="01011112222")
        self.assertEqual(teacher.name, "김선생")

    def test_phone_change_syncs_teacher(self):
        """Staff 전화번호 변경 → Teacher.phone도 변경됨."""
        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"phone": "01099998888"},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        teacher = Teacher.objects.get(tenant=self.tenant, name="김강사")
        self.assertEqual(teacher.phone, "01099998888")

    def test_name_and_phone_change_together(self):
        """이름 + 전화번호 동시 변경."""
        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"name": "박강사", "phone": "01033334444"},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        self.assertFalse(Teacher.objects.filter(tenant=self.tenant, name="김강사").exists())
        teacher = Teacher.objects.get(tenant=self.tenant, name="박강사")
        self.assertEqual(teacher.phone, "01033334444")


class TestStaffTeacherDeactivateSync(TestCase):
    """이슈 3: 이름 변경 + 비활성화 동시 요청 시 Teacher 비활성화도 정확히 동작."""

    def setUp(self):
        self.tenant = _make_tenant()
        self.staff = _create_staff_teacher(self.tenant, name="이강사", phone="01055556666")

    def test_deactivate_syncs_teacher(self):
        """Staff 비활성화 → Teacher.is_active=False."""
        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"is_active": False},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        teacher = Teacher.objects.get(tenant=self.tenant, name="이강사")
        self.assertFalse(teacher.is_active)

    def test_reactivate_syncs_teacher(self):
        """Staff 재활성화 → Teacher.is_active=True."""
        self.staff.is_active = False
        self.staff.save(update_fields=["is_active"])
        Teacher.objects.filter(tenant=self.tenant, name="이강사").update(is_active=False)

        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"is_active": True},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        teacher = Teacher.objects.get(tenant=self.tenant, name="이강사")
        self.assertTrue(teacher.is_active)

    def test_name_change_and_deactivate_simultaneously(self):
        """이름 변경 + 비활성화 동시 → Teacher가 새 이름으로 비활성화됨."""
        request = _make_request(self.tenant, self.staff.user)
        serializer = StaffCreateUpdateSerializer(
            self.staff,
            data={"name": "최강사", "is_active": False},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.update(self.staff, serializer.validated_data)

        # 구 이름 Teacher는 없어야 함
        self.assertFalse(Teacher.objects.filter(tenant=self.tenant, name="이강사").exists())
        # 새 이름 Teacher가 비활성 상태여야 함
        teacher = Teacher.objects.get(tenant=self.tenant, name="최강사")
        self.assertFalse(teacher.is_active)


class TestStaffRoleDetection(TestCase):
    """Staff role 판별: Teacher 존재 시 TEACHER, 없으면 ASSISTANT."""

    def setUp(self):
        self.tenant = _make_tenant()

    def test_role_teacher_when_teacher_exists(self):
        """Teacher 레코드가 있으면 role=TEACHER."""
        staff = _create_staff_teacher(self.tenant, name="강사A", phone="01077778888")
        request = _make_request(self.tenant, staff.user)
        serializer = StaffListSerializer(staff, context={"request": request})
        self.assertEqual(serializer.data["role"], "TEACHER")

    def test_role_assistant_when_no_teacher(self):
        """Teacher 레코드가 없으면 role=ASSISTANT."""
        staff = Staff.objects.create(tenant=self.tenant, name="조교A", phone="01099990000")
        request = _make_request(self.tenant)
        serializer = StaffListSerializer(staff, context={"request": request})
        self.assertEqual(serializer.data["role"], "ASSISTANT")

    def test_role_stays_teacher_after_name_change(self):
        """이름 변경 후에도 Teacher 연동이 유지되어 role=TEACHER."""
        staff = _create_staff_teacher(self.tenant, name="원래이름", phone="01012340000")
        request = _make_request(self.tenant, staff.user)

        # 이름 변경
        update_ser = StaffCreateUpdateSerializer(
            staff,
            data={"name": "변경이름"},
            partial=True,
            context={"request": request},
        )
        update_ser.is_valid(raise_exception=True)
        update_ser.update(staff, update_ser.validated_data)

        staff.refresh_from_db()
        list_ser = StaffListSerializer(staff, context={"request": request})
        self.assertEqual(list_ser.data["role"], "TEACHER")


class TestStaffDeletePolicy(TestCase):
    """Staff 삭제 정책 테스트."""

    def setUp(self):
        self.tenant = _make_tenant()

    def test_delete_removes_teacher(self):
        """Staff 삭제 → 대응 Teacher도 삭제."""
        staff = _create_staff_teacher(self.tenant, name="삭제대상", phone="01000001111")
        request = _make_request(self.tenant, staff.user)
        serializer = StaffCreateUpdateSerializer(
            staff, context={"request": request},
        )
        serializer.delete(staff)

        self.assertFalse(Staff.objects.filter(id=staff.id).exists())
        self.assertFalse(Teacher.objects.filter(tenant=self.tenant, name="삭제대상").exists())

    def test_owner_cannot_be_deleted(self):
        """Owner는 삭제 불가."""
        owner_user = User.objects.create_user(
            username=f"t{self.tenant.id}_owner",
            password="test1234",
            name="원장",
        )
        TenantMembership.objects.create(
            tenant=self.tenant, user=owner_user, role="owner", is_active=True,
        )
        staff = Staff.objects.create(
            tenant=self.tenant, user=owner_user, name="원장", phone="",
        )

        from rest_framework.exceptions import ValidationError
        serializer = StaffCreateUpdateSerializer(
            staff, context={"request": _make_request(self.tenant, owner_user)},
        )
        with self.assertRaises(ValidationError):
            serializer.delete(staff)


class TestStaffPasswordChange(TestCase):
    """비밀번호 변경 엔드포인트 (실제 view 호출)."""

    def setUp(self):
        self.tenant = _make_tenant()
        self.staff = _create_staff_teacher(self.tenant, name="비번테스트", phone="01022223333")

    def _call_change_password(self, staff_id, password_data):
        from rest_framework.test import APIRequestFactory
        from apps.domains.staffs.views import StaffViewSet
        factory = APIRequestFactory()
        request = factory.post(f"/staffs/{staff_id}/change-password/", password_data, format="json")
        request.tenant = self.tenant
        request.user = self.staff.user
        view = StaffViewSet.as_view({"post": "change_password"})
        return view(request, pk=staff_id)

    def test_password_change_works(self):
        """비밀번호 변경 후 새 비밀번호로 인증 가능."""
        resp = self._call_change_password(self.staff.id, {"password": "new_password_123"})
        self.assertEqual(resp.status_code, 200)
        self.staff.user.refresh_from_db()
        self.assertTrue(self.staff.user.check_password("new_password_123"))

    def test_password_too_short(self):
        """4자 미만 비밀번호는 거부."""
        resp = self._call_change_password(self.staff.id, {"password": "ab"})
        self.assertEqual(resp.status_code, 400)

    def test_password_empty(self):
        """빈 비밀번호는 거부."""
        resp = self._call_change_password(self.staff.id, {"password": ""})
        self.assertEqual(resp.status_code, 400)

    def test_password_no_user(self):
        """계정 없는 Staff는 비밀번호 변경 불가."""
        staff_no_user = Staff.objects.create(
            tenant=self.tenant, name="계정없음", phone="01099990000",
        )
        resp = self._call_change_password(staff_no_user.id, {"password": "test1234"})
        self.assertEqual(resp.status_code, 400)
