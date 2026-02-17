# PATH: apps/domains/students/serializers.py

import random
import string

from rest_framework import serializers

from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student, Tag
from apps.domains.students.ps_number import _generate_unique_ps_number


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = "__all__"
        ref_name = "StudentTagSerializer"


class EnrollmentSerializer(serializers.ModelSerializer):
    lecture_name = serializers.CharField(source="lecture.title", read_only=True)
    lecture_color = serializers.CharField(source="lecture.color", read_only=True, default="#3b82f6")
    lecture_chip_label = serializers.CharField(
        source="lecture.chip_label",
        read_only=True,
        allow_blank=True,
        default="",
    )

    class Meta:
        model = Enrollment
        fields = "__all__"
        ref_name = "StudentEnrollment"


class StudentListSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    enrollments = EnrollmentSerializer(many=True, read_only=True)
    is_enrolled = serializers.SerializerMethodField()
    profile_photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = "__all__"
        ref_name = "StudentList"

    def get_profile_photo_url(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.profile_photo.url)
        return obj.profile_photo.url

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data["profile_photo_url"] = self.get_profile_photo_url(obj)
        return data

    def get_is_enrolled(self, obj):
        request = self.context.get("request")
        if not request:
            return False

        lecture_id = request.query_params.get("lecture")
        if lecture_id:
            try:
                lid = int(lecture_id)
            except (TypeError, ValueError):
                return False
            return obj.enrollments.filter(lecture_id=lid).exists()

        return False


class StudentDetailSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    enrollments = EnrollmentSerializer(many=True, read_only=True)
    profile_photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = "__all__"
        ref_name = "StudentDetail"

    def get_profile_photo_url(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.profile_photo.url)
        return obj.profile_photo.url

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data["profile_photo_url"] = self.get_profile_photo_url(obj)
        return data


class AddTagSerializer(serializers.Serializer):
    tag_id = serializers.IntegerField()


class StudentBulkItemSerializer(serializers.Serializer):
    """엑셀 일괄 등록용 단일 학생 데이터"""

    name = serializers.CharField(allow_blank=False, trim_whitespace=True)
    phone = serializers.CharField(allow_blank=True, trim_whitespace=True, required=False, default="")
    parent_phone = serializers.CharField(allow_blank=False, trim_whitespace=True)
    uses_identifier = serializers.BooleanField(required=False, default=False)
    gender = serializers.CharField(allow_blank=True, default="")
    school_type = serializers.ChoiceField(
        choices=[("HIGH", "고등"), ("MIDDLE", "중등")],
        default="HIGH",
    )
    school = serializers.CharField(allow_blank=True, default="", required=False)
    high_school_class = serializers.CharField(allow_blank=True, default="", required=False)
    major = serializers.CharField(allow_blank=True, default="", required=False)
    grade = serializers.IntegerField(allow_null=True, required=False)
    memo = serializers.CharField(allow_blank=True, default="", required=False)
    is_managed = serializers.BooleanField(default=True, required=False)

    def validate_phone(self, value):
        # 학생 전화번호는 선택사항
        if not value:
            return None
        v = str(value or "").replace(" ", "").replace("-", "").replace(".", "")
        if v and (len(v) != 11 or not v.startswith("010")):
            raise serializers.ValidationError("전화번호는 010XXXXXXXX 11자리여야 합니다.")
        return v if v else None

    def validate_parent_phone(self, value):
        v = str(value or "").replace(" ", "").replace("-", "").replace(".", "")
        if not v or len(v) != 11 or not v.startswith("010"):
            raise serializers.ValidationError("학부모 전화번호는 010XXXXXXXX 11자리여야 합니다.")
        return v


class StudentBulkCreateSerializer(serializers.Serializer):
    initial_password = serializers.CharField(min_length=4, write_only=True)
    students = StudentBulkItemSerializer(many=True)
    send_welcome_message = serializers.BooleanField(required=False, default=False)


class StudentCreateSerializer(serializers.ModelSerializer):
    initial_password = serializers.CharField(
        write_only=True,
        required=True,
        min_length=4,
    )
    send_welcome_message = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )
    no_phone = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
        help_text="True면 식별자로 가입 (uses_identifier=True)",
    )
    ps_number = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="미입력 시 임의 6자리 자동 부여 (학생이 추후 변경 가능)",
    )

    class Meta:
        model = Student
        exclude = ("tenant", "user")

    def _require(self, attrs, key: str):
        v = attrs.get(key)
        if v is None:
            raise serializers.ValidationError({key: "필수입니다."})
        if isinstance(v, str) and not v.strip():
            raise serializers.ValidationError({key: "필수입니다."})
        return v

    def validate_phone(self, value):
        # 학생 전화번호는 선택사항 (없으면 None)
        if not value or not str(value).strip():
            return None

        from academy.adapters.db.django import repositories_students as student_repo
        if student_repo.user_filter_phone_exists(value):
            raise serializers.ValidationError("이미 사용 중인 전화번호입니다.")

        return value

    # omr_code는 validate에서 자동 설정되므로 별도 validate 불필요

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None:
            raise serializers.ValidationError("Tenant가 resolve되지 않았습니다.")

        ps_number_raw = attrs.get("ps_number") or ""
        ps_number = str(ps_number_raw).strip() if ps_number_raw else ""
        if not ps_number:
            try:
                ps_number = _generate_unique_ps_number()
            except ValueError as e:
                raise serializers.ValidationError({"ps_number": str(e)})
        parent_phone = str(self._require(attrs, "parent_phone")).strip()
        name = str(self._require(attrs, "name")).strip()
        phone = attrs.get("phone")
        phone_str = str(phone).strip() if phone else None

        # OMR 코드: 학생 전화번호가 있으면 학생 전화번호 8자리, 없으면 부모 전화번호 8자리
        if phone_str and len(phone_str) >= 8:
            omr_code = phone_str[-8:]
        elif parent_phone and len(parent_phone) >= 8:
            omr_code = parent_phone[-8:]
        else:
            raise serializers.ValidationError({"omr_code": "학생 전화번호 또는 부모 전화번호가 필요합니다."})

        attrs["ps_number"] = ps_number
        attrs["omr_code"] = omr_code
        attrs["phone"] = phone_str if phone_str else None
        attrs["parent_phone"] = parent_phone
        attrs["name"] = name

        # 사용자가 직접 입력한 경우에만 중복 체크 (자동 생성은 이미 User 중복 검사됨)
        from academy.adapters.db.django import repositories_students as student_repo
        if ps_number_raw and student_repo.student_filter_tenant_ps(tenant, ps_number).exists():
            raise serializers.ValidationError({"ps_number": "이미 사용 중인 PS 번호입니다."})

        attrs["uses_identifier"] = attrs.pop("no_phone", False) or (phone_str is None)
        return attrs


class StudentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        exclude = ("tenant", "user")

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None:
            raise serializers.ValidationError("Tenant가 resolve되지 않았습니다.")

        instance = self.instance

        phone = attrs.get("phone", instance.phone)
        parent_phone = attrs.get("parent_phone", instance.parent_phone)
        ps_number = attrs.get("ps_number", instance.ps_number)

        # OMR 코드: 학생 전화번호가 있으면 학생 전화번호 8자리, 없으면 부모 전화번호 8자리
        phone_str = str(phone).strip() if phone else None
        parent_phone_str = str(parent_phone).strip() if parent_phone else None
        
        if phone_str and len(phone_str) >= 8:
            omr_code = phone_str[-8:]
        elif parent_phone_str and len(parent_phone_str) >= 8:
            omr_code = parent_phone_str[-8:]
        else:
            omr_code = attrs.get("omr_code", instance.omr_code)  # 기존 값 유지

        attrs["omr_code"] = omr_code

        from academy.adapters.db.django import repositories_students as student_repo
        if ps_number:
            if student_repo.student_filter_tenant_ps_exclude_id(
                tenant, ps_number, instance.id
            ).exists():
                raise serializers.ValidationError({"ps_number": "이미 사용 중인 PS 번호입니다."})

        return attrs
