"""Administrative roster operations for newly made, credentialless Dawon demo students."""

import re
from uuid import uuid5

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.core.models import TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student, Tag
from apps.domains.students.services.lifecycle import StudentLifecycleError, restore_student, soft_delete_student
from apps.support.students.namespace_lock import lock_student_creation_tenant_reference

from .dawon_staff import StaffRequest


IDENTITY = re.compile(r"dw-demo-[0-9a-f]{16}")
SCHOOL_FIELD = {"ELEMENTARY": "elementary_school", "MIDDLE": "middle_school", "HIGH": "high_school"}
PROFILE_FIELDS = ("name", "gender", "school_type", "grade", "high_school_class", "major", "memo")


class ProfileInput(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    gender = serializers.ChoiceField(choices=("M", "F"), allow_null=True, required=False)
    school_type = serializers.ChoiceField(choices=tuple(SCHOOL_FIELD), required=False)
    school = serializers.CharField(max_length=100, allow_blank=True, required=False)
    grade = serializers.IntegerField(min_value=1, max_value=6, allow_null=True, required=False)
    high_school_class = serializers.CharField(max_length=100, allow_blank=True, required=False)
    major = serializers.CharField(max_length=50, allow_blank=True, required=False)
    memo = serializers.CharField(max_length=2000, allow_blank=True, required=False)
    tag_ids = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=2**63-1),
                                    max_length=30, required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise ValidationError("데모 명단에는 학생의 학습 정보만 입력할 수 있습니다.")
        return super().to_internal_value(data)

    def validate(self, attrs):
        original = self.instance
        school_type = attrs.get("school_type", original.school_type if original else "HIGH")
        grade = attrs.get("grade", original.grade if original else None)
        if grade and school_type != "ELEMENTARY" and grade > 3:
            raise ValidationError({"grade": "중학교와 고등학교 학년은 1~3학년입니다."})
        if "tag_ids" in attrs:
            ids = set(attrs["tag_ids"])
            tags = Tag.objects.filter(tenant=self.context["tenant"], pk__in=ids)
            if tags.count() != len(ids):
                raise ValidationError({"tag_ids": "이 데모에서 사용할 수 없는 태그가 있습니다."})
            attrs["tag_ids"] = sorted(ids)
        return attrs


class ImportInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    students = ProfileInput(many=True, allow_empty=False, max_length=100)


def owned_students(tenant):
    return Student.objects.filter(tenant=tenant, user__tenant=tenant, user__is_staff=False,
                                  user__is_superuser=False, user__password__startswith="!",
                                  parent__isnull=True, phone__isnull=True, parent_phone="").exclude(pk=2)


def owned_identity(student):
    identifier = student.ps_number.removeprefix(f"_del_{student.pk}_") if student.deleted_at else student.ps_number
    privileged = TenantMembership.objects.filter(user_id=student.user_id, is_active=True).exclude(
        tenant_id=student.tenant_id, role="student"
    ).exists()
    return bool(IDENTITY.fullmatch(identifier)
                and student.pk != 2
                and student.user.tenant_id == student.tenant_id
                and not student.user.is_staff and not student.user.is_superuser
                and not student.user.has_usable_password()
                and not student.phone and not student.parent_phone and not student.parent_id
                and student.user.username == user_internal_username(student.tenant, student.ps_number)
                and not privileged)


def row(student):
    return {"id": student.pk, "deleted": student.deleted_at is not None,
            "school": getattr(student, SCHOOL_FIELD[student.school_type]) or "",
            "tag_ids": list(student.tags.filter(tenant_id=student.tenant_id).values_list("id", flat=True)),
            **{field: getattr(student, field) for field in ("name", "gender", "school_type", "grade")},
            **{field: getattr(student, field) or "" for field in ("high_school_class", "major", "memo")}}


def set_profile(student, values):
    school_type = values.get("school_type", student.school_type)
    fields = {field: values[field] for field in PROFILE_FIELDS if field in values}
    if "school" in values:
        fields[SCHOOL_FIELD[school_type]] = values["school"]
    elif school_type != student.school_type:
        fields[SCHOOL_FIELD[school_type]] = None
    for field, value in fields.items():
        setattr(student, field, value)
    return list(fields)


class DawonRosterView(StaffRequest):
    parser_classes = [JSONParser]

    @extend_schema(exclude=True)
    def get(self, request):
        deleted = request.query_params.get("deleted") == "true"
        students = owned_students(request.tenant).filter(
            deleted_at__isnull=not deleted, ps_number__regex=r"^(?:_del_[0-9]+_)?dw-demo-[0-9a-f]{16}$"
        ).select_related("tenant", "user").order_by("-id")
        tags = Tag.objects.filter(tenant=request.tenant).order_by("name").values("id", "name", "color")
        return Response({"rows": [row(student) for student in students if owned_identity(student)], "tags": list(tags)})

    @extend_schema(exclude=True)
    @transaction.atomic
    def post(self, request):
        payload = ImportInput(data=request.data, context={"tenant": request.tenant})
        payload.is_valid(raise_exception=True)
        students = []
        lock_student_creation_tenant_reference(tenant_id=request.tenant.id)
        request_id = payload.validated_data["request_id"]
        for index, values in enumerate(payload.validated_data["students"]):
            identifier = "dw-demo-" + uuid5(request_id, str(index)).hex[:16]
            user, created = get_user_model().objects.get_or_create(
                username=user_internal_username(request.tenant, identifier),
                defaults={"tenant": request.tenant, "name": values["name"]},
            )
            if not created:
                student = owned_students(request.tenant).filter(user=user, ps_number=identifier).first()
                if student is None or not owned_identity(student):
                    raise ValidationError("이미 처리된 등록입니다. 명단을 확인해 주세요.")
                students.append(row(student))
                continue
            user.set_unusable_password()
            user.save(update_fields=["password"])
            student = Student(tenant=request.tenant, user=user, ps_number=identifier,
                              omr_code=f"{int(identifier[-8:], 16) % 100_000_000:08d}", parent_phone="")
            set_profile(student, values)
            student.save()
            TenantMembership.ensure_active(tenant=request.tenant, user=user, role="student")
            if "tag_ids" in values:
                student.tags.set(values["tag_ids"])
            students.append(row(student))
        return Response({"rows": students}, status=201)


class DawonRosterStudentView(StaffRequest):
    parser_classes = [JSONParser]

    def student(self, request, student_id, *, deleted=False):
        student = owned_students(request.tenant).filter(
            pk=student_id, deleted_at__isnull=not deleted
        ).select_related("tenant", "user").first()
        if student is None:
            raise NotFound()
        lock_student_creation_tenant_reference(tenant_id=request.tenant.id)
        user = get_user_model().objects.select_for_update().get(pk=student.user_id)
        locked = Student.objects.select_for_update().get(pk=student.pk)
        if locked.user_id != user.pk or bool(locked.deleted_at) != deleted or not owned_identity(locked):
            raise NotFound()
        return locked

    @extend_schema(exclude=True)
    @transaction.atomic
    def patch(self, request, student_id):
        student = self.student(request, student_id)
        data = ProfileInput(student, data=request.data, partial=True, context={"tenant": request.tenant})
        data.is_valid(raise_exception=True)
        values = data.validated_data
        fields = set_profile(student, values)
        if fields:
            student.save(update_fields=fields)
        if "name" in fields:
            student.user.name = student.name
            student.user.save(update_fields=["name"])
        if "tag_ids" in values:
            student.tags.set(values["tag_ids"])
        return Response(row(student))

    @extend_schema(exclude=True)
    @transaction.atomic
    def delete(self, request, student_id):
        student = self.student(request, student_id)
        try:
            soft_delete_student(student, tenant=request.tenant)
        except StudentLifecycleError as error:
            raise ValidationError(error.detail) from error
        return Response(status=204)

    @extend_schema(exclude=True)
    @transaction.atomic
    def post(self, request, student_id):
        student = self.student(request, student_id, deleted=True)
        try:
            restored = restore_student(student, tenant=request.tenant)
        except StudentLifecycleError as error:
            raise ValidationError(error.detail) from error
        return Response(row(restored.student))


CREDENTIAL_PREFIX = "!dawon-demo:"


class CredentialInput(serializers.Serializer):
    action = serializers.ChoiceField(choices=("set", "check"))
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False, write_only=True)


class CredentialThrottle(SimpleRateThrottle):
    scope = "dawon_demo_credential"
    rate = "10/min"

    def get_cache_key(self, request, view):
        if not request.user.is_authenticated:
            return None
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk}


class DawonRosterCredentialView(StaffRequest):
    parser_classes = [JSONParser]
    throttle_classes = [CredentialThrottle]

    @extend_schema(exclude=True)
    @transaction.atomic
    def post(self, request, student_id):
        payload = CredentialInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        student = DawonRosterStudentView().student(request, student_id)
        user = student.user
        if data["action"] == "set":
            # Leading "!" preserves the original unusable-account contract.
            user.password = CREDENTIAL_PREFIX + make_password(data["password"], hasher="pbkdf2_sha256")
            user.save(update_fields=["password"])
            return Response({"configured": True})
        configured = user.password.startswith(CREDENTIAL_PREFIX)
        matches = configured and check_password(data["password"], user.password[len(CREDENTIAL_PREFIX):])
        return Response({"matches": matches})
