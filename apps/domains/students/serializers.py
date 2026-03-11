# PATH: apps/domains/students/serializers.py

from rest_framework import serializers

from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student, Tag, StudentRegistrationRequest
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
    omr_code = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="validate()에서 자동 생성 (학생/부모 전화번호 뒤 8자리)",
    )

    def validate_parent_phone(self, value):
        v = str(value or "").strip().replace(" ", "").replace("-", "").replace(".", "")
        if not v or len(v) != 11 or not v.startswith("010"):
            raise serializers.ValidationError(
                "학부모 전화번호는 010XXXXXXXX 11자리여야 합니다."
            )
        return v

    class Meta:
        model = Student
        exclude = ("tenant", "user")
        read_only_fields = ("deleted_at", "profile_photo")

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

        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None:
            raise serializers.ValidationError("Tenant가 resolve되지 않았습니다.")

        from academy.adapters.db.django import repositories_students as student_repo
        if student_repo.user_filter_phone_exists(value, tenant=tenant):
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
                ps_number = _generate_unique_ps_number(tenant=tenant)
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

        # 사용자가 직접 입력한 경우에만 중복 체크 (활성 학생만 — 삭제된 학생 PS는 재사용 가능)
        from academy.adapters.db.django import repositories_students as student_repo
        if ps_number_raw and student_repo.student_filter_tenant_ps_number(tenant, ps_number).exists():
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


# ========== 학생 가입 신청 (로그인 전 회원가입) ==========


def _normalize_phone(value):
    if not value:
        return ""
    return str(value).strip().replace(" ", "").replace("-", "").replace(".", "")


class RegistrationRequestCreateSerializer(serializers.Serializer):
    """학생이 로그인 페이지에서 제출하는 가입 신청 (필수 필드만)"""

    name = serializers.CharField(max_length=50, trim_whitespace=True)
    username = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
        trim_whitespace=True,
    )
    initial_password = serializers.CharField(min_length=4, max_length=128, write_only=True)
    parent_phone = serializers.CharField(max_length=20)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True, default="")
    school_type = serializers.ChoiceField(
        choices=[("HIGH", "고등"), ("MIDDLE", "중등")],
        default="HIGH",
    )
    high_school = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    middle_school = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    high_school_class = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    major = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    grade = serializers.IntegerField(required=False, allow_null=True)
    gender = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="", max_length=1)
    memo = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="", max_length=255)
    origin_middle_school = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="", max_length=100)

    def validate_parent_phone(self, value):
        v = _normalize_phone(value)
        if not v or len(v) != 11 or not v.startswith("010"):
            raise serializers.ValidationError("학부모 전화번호는 010XXXXXXXX 11자리여야 합니다.")
        return v

    def validate_phone(self, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        v = _normalize_phone(str(value))
        if v and (len(v) != 11 or not v.startswith("010")):
            raise serializers.ValidationError("전화번호는 010XXXXXXXX 11자리여야 합니다.")
        return v if v else None

    def validate(self, attrs):
        attrs["parent_phone"] = _normalize_phone(attrs["parent_phone"])
        attrs["phone"] = attrs.get("phone") or None
        if attrs.get("phone"):
            attrs["phone"] = _normalize_phone(attrs["phone"]) or None
        # null → 빈 문자열로 통일 (모델은 null 허용이지만 저장 시 빈 문자열도 허용)
        for key in ("username", "high_school", "middle_school", "high_school_class", "major", "gender", "memo", "address", "origin_middle_school"):
            if attrs.get(key) is None:
                attrs[key] = ""

        # 회원가입 시 모든 필드 필수 입력 (계열 제외) — Limglish 등 운영 요구
        signup_required = {
            "name": "이름",
            "initial_password": "비밀번호",
            "parent_phone": "학부모 연락처",
            "phone": "휴대전화",
            "high_school_class": "반",
            "grade": "학년",
            "gender": "성별",
            "address": "주소",
        }
        school_type = attrs.get("school_type") or "HIGH"
        if school_type == "HIGH":
            signup_required["high_school"] = "고등학교명"
            signup_required["origin_middle_school"] = "출신중학교"
        else:
            signup_required["middle_school"] = "중학교명"

        for key, label in signup_required.items():
            val = attrs.get(key)
            if key == "grade":
                if val is None or (isinstance(val, str) and str(val).strip() == ""):
                    raise serializers.ValidationError({key: f"{label}을(를) 입력해 주세요."})
            elif isinstance(val, str):
                if not val.strip():
                    raise serializers.ValidationError({key: f"{label}을(를) 입력해 주세요."})
            elif val is None:
                raise serializers.ValidationError({key: f"{label}을(를) 입력해 주세요."})

        return attrs


class RegistrationRequestListSerializer(serializers.ModelSerializer):
    """스태프용 가입 신청 목록/상세 (initial_password 제외)"""

    class Meta:
        model = StudentRegistrationRequest
        exclude = ("initial_password",)
