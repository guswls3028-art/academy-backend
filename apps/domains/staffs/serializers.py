# PATH: apps/domains/staffs/serializers.py
# 원칙: 테넌트별 완전 격리. 직원/User는 해당 테넌트 컨텍스트 내에서만 사용.
import logging

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError

from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
    WorkMonthLock,
    PayrollSnapshot,
)
from academy.adapters.db.django import repositories_staffs as staff_repo
from academy.adapters.db.django import repositories_teachers as teacher_repo
from academy.adapters.db.django import repositories_students as students_repo
from academy.adapters.db.django import repositories_core as core_repo

User = get_user_model()

# ---------------------------
# WorkType
# ---------------------------

class WorkTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkType
        fields = [
            "id",
            "name",
            "base_hourly_wage",
            "color",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffWorkTypeDefinition"


# ---------------------------
# StaffWorkType
# ---------------------------

class StaffWorkTypeSerializer(serializers.ModelSerializer):
    staff = serializers.PrimaryKeyRelatedField(
        queryset=Staff.objects.none(),
    )
    work_type = WorkTypeSerializer(read_only=True)
    work_type_id = serializers.PrimaryKeyRelatedField(
        source="work_type",
        queryset=staff_repo.work_type_empty_queryset(),
        write_only=True,
    )
    effective_hourly_wage = serializers.IntegerField(read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        tenant = getattr(request, "tenant", None) if request else None
        self.fields["staff"].queryset = (
            staff_repo.staff_queryset_tenant(tenant) if tenant else Staff.objects.none()
        )
        self.fields["work_type_id"].queryset = (
            staff_repo.work_type_queryset_tenant(tenant) if tenant else staff_repo.work_type_empty_queryset()
        )

    class Meta:
        model = StaffWorkType
        fields = [
            "id",
            "staff",
            "work_type",
            "work_type_id",
            "hourly_wage",
            "effective_hourly_wage",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        ref_name = "StaffWorkType"


# ---------------------------
# Staff (LIST / DETAIL)
# ---------------------------

class StaffListSerializer(serializers.ModelSerializer):
    staff_work_types = StaffWorkTypeSerializer(many=True, read_only=True)
    role = serializers.SerializerMethodField()
    profile_photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Staff
        fields = [
            "id",
            "name",
            "phone",
            "profile_photo_url",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffList"

    def get_profile_photo_url(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.profile_photo.url)
        return obj.profile_photo.url

    def get_role(self, obj):
        # ViewSet.list가 (name, phone) 키 집합을 컨텍스트로 주입하면 O(1) 룩업으로 N+1 회피.
        # list 쿼리셋은 이미 owner Staff를 제외하므로 owner 분기 불필요.
        teacher_keys = self.context.get("teacher_keys")
        if teacher_keys is not None:
            if (obj.name, obj.phone or "") in teacher_keys:
                return "TEACHER"
            return "ASSISTANT"
        # 컨텍스트가 없는 경우(단독 사용): 안전한 폴백.
        if getattr(obj, "user_id", None):
            if core_repo.membership_exists_staff(obj.tenant, obj.user, staff_roles=("owner",)):
                return "OWNER"
        if teacher_repo.teacher_exists_tenant_name_phone(obj.tenant, obj.name, obj.phone or ""):
            return "TEACHER"
        return "ASSISTANT"


class StaffDetailSerializer(serializers.ModelSerializer):
    staff_work_types = StaffWorkTypeSerializer(many=True, read_only=True)
    role = serializers.SerializerMethodField()
    profile_photo_url = serializers.SerializerMethodField()

    user_username = serializers.SerializerMethodField()

    def get_user_username(self, obj):
        if not getattr(obj, "user", None):
            return ""
        from apps.core.models.user import user_display_username
        return user_display_username(obj.user)
    user_is_staff = serializers.BooleanField(
        source="user.is_staff",
        read_only=True,
    )

    class Meta:
        model = Staff
        fields = [
            "id",
            "user",
            "user_username",
            "user_is_staff",
            "name",
            "phone",
            "profile_photo_url",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffDetail"

    def get_profile_photo_url(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.profile_photo.url)
        return obj.profile_photo.url

    def get_role(self, obj):
        # 오너(owner) 멤버십이 있는 Staff → "OWNER"
        if getattr(obj, "user_id", None):
            if core_repo.membership_exists_staff(obj.tenant, obj.user, staff_roles=("owner",)):
                return "OWNER"
        if teacher_repo.teacher_exists_tenant_name_phone(obj.tenant, obj.name, obj.phone or ""):
            return "TEACHER"
        return "ASSISTANT"


# ======================================================
# 🔥 Staff CREATE / UPDATE / DELETE (ROLE 포함)
# ======================================================

class StaffCreateUpdateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=[("TEACHER", "강사"), ("ASSISTANT", "조교")],
        write_only=True,
        required=True,
    )
    username = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=150)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=128)

    def validate_phone(self, value):
        if value:
            return value.replace("-", "").replace(" ", "").strip()
        return value

    class Meta:
        model = Staff
        fields = [
            "id",
            "user",
            "username",
            "password",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
        ]
        read_only_fields = ["id", "user"]
        ref_name = "StaffWrite"

    def validate(self, attrs):
        if "user" in getattr(self, "initial_data", {}):
            raise serializers.ValidationError(
                {"user": "직원 계정은 직접 연결할 수 없습니다. 아이디/초기 비밀번호로 생성해 주세요."}
            )
        initial = getattr(self, "initial_data", {})
        username = str(initial.get("username") or "").strip()
        password = str(initial.get("password") or "")
        if bool(username) != bool(password):
            raise serializers.ValidationError(
                {
                    "username": "로그인 아이디와 초기 비밀번호는 함께 입력하거나 둘 다 비워 주세요.",
                    "password": "로그인 아이디와 초기 비밀번호는 함께 입력하거나 둘 다 비워 주세요.",
                }
            )
        return attrs

    # =========================
    # CREATE
    # =========================
    def create(self, validated_data):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if not tenant:
            raise serializers.ValidationError(
                {"detail": "테넌트를 확인할 수 없습니다. 요청 헤더 또는 접속 주소를 확인해 주세요."}
            )

        role = validated_data.pop("role")
        username = (validated_data.pop("username", None) or "").strip()
        password = (validated_data.pop("password", None) or "")

        try:
            with transaction.atomic():
                user = None
                if username and password:
                    user = students_repo.user_create_user(
                        username=username,
                        password=password,
                        tenant=tenant,
                        name=validated_data.get("name") or username,
                        phone=validated_data.get("phone") or "",
                    )
                    core_repo.membership_ensure_active(
                        tenant=tenant,
                        user=user,
                        role="teacher" if role == "TEACHER" else "staff",
                        protected_existing_roles=("owner", "admin"),
                    )
                    validated_data["user"] = user

                validated_data["tenant"] = tenant
                staff = super().create(validated_data)

                if role == "TEACHER":
                    self._create_teacher(staff)
                    self._grant_user_staff_permission(staff)

                return staff
        except IntegrityError as e:
            err_msg = str(e).lower()
            if "username" in err_msg or ("unique" in err_msg and "username" in err_msg):
                raise serializers.ValidationError(
                    {"username": "이미 사용 중인 로그인 아이디입니다."}
                )
            if "phone" in err_msg or "uniq_staff_phone" in err_msg or "uniq_teacher_phone" in err_msg:
                raise serializers.ValidationError(
                    {"phone": "이미 등록된 전화번호입니다."}
                )
            raise serializers.ValidationError(
                {"detail": "이미 등록된 정보와 충돌합니다. 로그인 아이디·전화번호를 확인해 주세요."}
            )
        except ValueError as e:
            raise serializers.ValidationError({"detail": str(e)})
        except Exception as e:
            logging.exception("Staff create failed: %s", e)
            raise serializers.ValidationError(
                {"detail": "직원 등록 중 오류가 발생했습니다. 입력값을 확인해 주세요."}
            )

    # =========================
    # UPDATE (Teacher + TenantMembership lifecycle synchronization)
    # =========================
    def update(self, instance, validated_data):
        requested_role = validated_data.pop("role", None)
        validated_data.pop("username", None)
        validated_data.pop("password", None)

        # Teacher 동기화를 위해 old 값 보존 (super().update() 전)
        old_name = instance.name
        old_phone = instance.phone or ""
        is_active_before = instance.is_active
        wants_reactivation = not is_active_before and validated_data.get("is_active") is True
        wants_deactivation = is_active_before and validated_data.get("is_active") is False
        membership = (
            core_repo.membership_get_full(instance.tenant, instance.user)
            if instance.user_id
            else None
        )
        if membership and membership.role in ("owner", "admin") and (
            validated_data.get("is_active") is False or wants_reactivation
        ):
            raise serializers.ValidationError(
                {"is_active": "대표/관리자 계정은 직원 화면에서 비활성화하거나 재활성화할 수 없습니다."}
            )
        if wants_reactivation and requested_role is None:
            raise serializers.ValidationError(
                {"role": "재활성화할 직원 역할(TEACHER 또는 ASSISTANT)을 명시해 주세요."}
            )
        if requested_role is not None and wants_deactivation:
            raise serializers.ValidationError(
                {"role": "비활성화와 역할 변경을 동시에 요청할 수 없습니다."}
            )
        if requested_role is not None and not is_active_before and not wants_reactivation:
            raise serializers.ValidationError(
                {"role": "비활성 직원의 역할은 재활성화 요청과 함께 지정해 주세요."}
            )

        try:
            with transaction.atomic():
                instance = Staff.objects.select_for_update().get(pk=instance.pk)
                staff = super().update(instance, validated_data)

                new_name = staff.name
                new_phone = staff.phone or ""
                name_or_phone_changed = (old_name != new_name) or (old_phone != new_phone)

                # 1) 이름/전화 변경 → Teacher 레코드 동기화 (old 값으로 찾아서 new 값으로 업데이트)
                if name_or_phone_changed:
                    teacher_repo.teacher_update_name_phone(
                        staff.tenant, old_name, old_phone, new_name, new_phone,
                    )

                # 2) 비활성화 → Teacher도 비활성화 (이름/전화 동기화 후이므로 new 값 사용)
                if is_active_before and staff.is_active is False:
                    teacher_repo.teacher_update_is_active_by_name_phone(
                        staff.tenant, new_name, new_phone, False,
                    )
                    if staff.user_id:
                        from apps.core.services.tenant_access import (
                            TenantAccessMutationError,
                            deactivate_tenant_membership,
                        )
                        try:
                            deactivate_tenant_membership(
                                user=staff.user,
                                tenant=staff.tenant,
                                allowed_roles=("teacher", "staff"),
                            )
                        except TenantAccessMutationError as exc:
                            raise serializers.ValidationError(
                                {"is_active": str(exc)}
                            ) from exc

                # 3) 재활성화 → Teacher도 활성화
                if not is_active_before and staff.is_active is True:
                    role = (
                        "teacher" if requested_role == "TEACHER" else
                        "staff"
                    )
                    if role == "teacher":
                        teacher_repo.teacher_ensure_active_by_name_phone(
                            staff.tenant, new_name, new_phone,
                        )
                    else:
                        teacher_repo.teacher_update_is_active_by_name_phone(
                            staff.tenant, new_name, new_phone, False,
                        )
                    if staff.user_id:
                        core_repo.membership_ensure_active(
                            tenant=staff.tenant,
                            user=staff.user,
                            role=role,
                            protected_existing_roles=("owner", "admin"),
                        )
                        from apps.core.services.tenant_access import reconcile_user_tenant_access
                        reconcile_user_tenant_access(staff.user)

                # Active role edits are a real lifecycle change, never a silent
                # no-op. Keep Teacher profile and membership role atomic.
                if is_active_before and staff.is_active and requested_role is not None:
                    role = "teacher" if requested_role == "TEACHER" else "staff"
                    if membership and membership.role in ("owner", "admin"):
                        raise serializers.ValidationError(
                            {"role": "대표/관리자 역할은 직원 화면에서 변경할 수 없습니다."}
                        )
                    if role == "teacher":
                        teacher_repo.teacher_ensure_active_by_name_phone(
                            staff.tenant, new_name, new_phone,
                        )
                    else:
                        teacher_repo.teacher_update_is_active_by_name_phone(
                            staff.tenant, new_name, new_phone, False,
                        )
                    if staff.user_id:
                        core_repo.membership_ensure_active(
                            tenant=staff.tenant,
                            user=staff.user,
                            role=role,
                            protected_existing_roles=("owner", "admin"),
                        )
                        from apps.core.services.tenant_access import reconcile_user_tenant_access
                        reconcile_user_tenant_access(staff.user)

                return staff
        except IntegrityError as e:
            err_msg = str(e).lower()
            if "phone" in err_msg or "uniq_staff_phone" in err_msg or "uniq_teacher_phone" in err_msg:
                raise serializers.ValidationError(
                    {"phone": "이미 등록된 전화번호입니다."}
                )
            raise serializers.ValidationError(
                {"detail": "정보 수정 중 충돌이 발생했습니다. 입력값을 확인해 주세요."}
            )
        except ValueError as e:
            raise serializers.ValidationError({"role": str(e)}) from e

    # =========================
    # DELETE (Staff + Teacher + User)
    # =========================
    def delete(self, instance):
        membership = (
            core_repo.membership_get_full(instance.tenant, instance.user)
            if instance.user_id
            else None
        )
        if membership and membership.is_active and membership.role in ("owner", "admin"):
            raise serializers.ValidationError("대표/관리자는 직원 화면에서 삭제할 수 없습니다.")

        user = instance.user
        tenant = instance.tenant

        with transaction.atomic():
            teacher_repo.teacher_delete_by_name_phone(tenant, instance.name, instance.phone or "")
            instance.delete()
            if user:
                # User를 hard-delete하지 않고 비활성화 + 해당 테넌트 멤버십만 제거.
                # hard-delete는 Student, Attendance 등을 cascade로 파괴할 수 있으므로 절대 금지.
                # User.is_active/is_staff 는 전역 속성이므로, 다른 테넌트 멤버십이 남아 있거나
                # User.tenant 가 이 Staff의 tenant가 아니면 절대 변경하지 않는다.
                from apps.core.services.tenant_access import deactivate_tenant_membership
                deactivate_tenant_membership(
                    user=user,
                    tenant=tenant,
                    allowed_roles=("teacher", "staff"),
                )

    # =========================
    # Helpers
    # =========================
    def _create_teacher(self, staff: Staff):
        teacher_repo.teacher_create(
            staff.tenant,
            staff.name,
            staff.phone or "",
            is_active=True,
        )

    def _grant_user_staff_permission(self, staff: Staff):
        if not staff.user:
            return

        user: User = staff.user
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])


# ---------------------------
# WorkRecord
# ---------------------------

class WorkRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    work_type_name = serializers.CharField(source="work_type.name", read_only=True)

    class Meta:
        model = WorkRecord
        fields = [
            "id",
            "staff",
            "staff_name",
            "work_type",
            "work_type_name",
            "date",
            "start_time",
            "end_time",
            "break_minutes",
            "meal_minutes",
            "work_hours",
            "amount",
            "adjustment_amount",
            "resolved_hourly_wage",
            "is_manually_edited",
            "memo",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["resolved_hourly_wage", "is_manually_edited", "created_at", "updated_at"]
        ref_name = "StaffWorkRecord"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            tenant = request.tenant
            self.fields["staff"].queryset = Staff.objects.filter(tenant=tenant)
            self.fields["work_type"].queryset = WorkType.objects.filter(tenant=tenant)
        else:
            self.fields["staff"].queryset = Staff.objects.none()
            self.fields["work_type"].queryset = WorkType.objects.none()


# ---------------------------
# ExpenseRecord
# ---------------------------

class ExpenseRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    approved_by_name = serializers.CharField(
        source="approved_by.username",
        read_only=True,
    )

    class Meta:
        model = ExpenseRecord
        fields = [
            "id",
            "staff",
            "staff_name",
            "date",
            "title",
            "amount",
            "memo",
            "status",
            "approved_at",
            "approved_by",
            "approved_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "approved_at",
            "approved_by",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffExpenseRecord"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            tenant = request.tenant
            self.fields["staff"].queryset = Staff.objects.filter(tenant=tenant)


# ---------------------------
# WorkMonthLock / Payroll
# ---------------------------

class WorkMonthLockSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    locked_by_name = serializers.CharField(source="locked_by.username", read_only=True)

    class Meta:
        model = WorkMonthLock
        fields = [
            "id",
            "staff",
            "staff_name",
            "year",
            "month",
            "is_locked",
            "locked_by",
            "locked_by_name",
            "created_at",
        ]
        read_only_fields = ["locked_by", "created_at"]
        ref_name = "WorkMonthLock"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        self.fields["staff"].queryset = Staff.objects.filter(tenant=tenant) if tenant else Staff.objects.none()

    def validate_staff(self, staff):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if not tenant or staff.tenant_id != tenant.id:
            raise serializers.ValidationError("해당 직원을 찾을 수 없습니다.")
        return staff


class PayrollSnapshotSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True)

    class Meta:
        model = PayrollSnapshot
        fields = [
            "id",
            "staff",
            "staff_name",
            "year",
            "month",
            "work_hours",
            "work_amount",
            "approved_expense_amount",
            "total_amount",
            "generated_by",
            "generated_by_name",
            "created_at",
        ]
        read_only_fields = fields
        ref_name = "PayrollSnapshot"
