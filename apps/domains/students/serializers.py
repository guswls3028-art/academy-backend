# PATH: apps/domains/students/serializers.py

from rest_framework import serializers

from apps.domains.students.models import Student, Tag
from apps.domains.enrollment.models import Enrollment
from apps.domains.interactions.counseling.models import Counseling
from apps.domains.interactions.questions.models import Question


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = "__all__"
        ref_name = "StudentTagSerializer"


class CounselingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Counseling
        fields = "__all__"
        ref_name = "StudentCounseling"


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = "__all__"
        ref_name = "StudentQuestion"


class EnrollmentSerializer(serializers.ModelSerializer):
    lecture_name = serializers.CharField(source="lecture.title", read_only=True)

    class Meta:
        model = Enrollment
        fields = "__all__"
        ref_name = "StudentEnrollment"


class StudentListSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    is_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = "__all__"
        ref_name = "StudentList"

    def get_is_enrolled(self, obj):
        request = self.context.get("request")
        if not request:
            return False

        lecture_id = request.query_params.get("lecture")
        if lecture_id:
            return obj.enrollments.filter(lecture_id=lecture_id).exists()

        return False


class StudentDetailSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    enrollments = EnrollmentSerializer(many=True, read_only=True)
    counselings = CounselingSerializer(many=True, read_only=True)
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Student
        fields = "__all__"
        ref_name = "StudentDetail"


class AddTagSerializer(serializers.Serializer):
    tag_id = serializers.IntegerField()


class StudentCreateSerializer(serializers.ModelSerializer):
    """
    ✅ 봉인 Create Serializer
    - 필수 입력 강제: ps_number, omr_code, phone, parent_phone, name
    - omr_code == phone[-8:] 일치 강제
    - tenant 내 ps_number / omr_code 중복 선제 차단
    - phone(username) 중복 선제 차단
    """

    initial_password = serializers.CharField(
        write_only=True,
        required=True,
        min_length=4,
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
        if not value or not str(value).strip():
            raise serializers.ValidationError("전화번호는 필수입니다.")

        from django.contrib.auth import get_user_model
        User = get_user_model()

        # ✅ 운영 봉인: phone = username 정책 유지 + 중복 차단
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("이미 사용 중인 전화번호입니다.")

        return value

    def validate_omr_code(self, value):
        v = str(value or "").strip()
        if len(v) != 8 or not v.isdigit():
            raise serializers.ValidationError("OMR 식별자는 숫자 8자리여야 합니다.")
        return v

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None:
            # tenant 없는 요청 자체를 봉인 (TenantMiddleware와 이중 가드)
            raise serializers.ValidationError("Tenant가 resolve되지 않았습니다.")

        ps_number = str(self._require(attrs, "ps_number")).strip()
        omr_code = str(self._require(attrs, "omr_code")).strip()
        phone = str(self._require(attrs, "phone")).strip()
        parent_phone = str(self._require(attrs, "parent_phone")).strip()
        name = str(self._require(attrs, "name")).strip()

        attrs["ps_number"] = ps_number
        attrs["omr_code"] = omr_code
        attrs["phone"] = phone
        attrs["parent_phone"] = parent_phone
        attrs["name"] = name

        # ✅ 핵심 요구사항: OMR 식별자 = 전화번호 뒤 8자리
        if len(phone) < 8 or phone[-8:] != omr_code:
            raise serializers.ValidationError(
                {"omr_code": "OMR 식별자는 전화번호 뒤 8자리와 일치해야 합니다."}
            )

        # ✅ tenant 내 유일성 선제 검증 (DB 에러 전에 명확한 메시지)
        if Student.objects.filter(tenant=tenant, ps_number=ps_number).exists():
            raise serializers.ValidationError({"ps_number": "이미 사용 중인 PS 번호입니다."})

        if Student.objects.filter(tenant=tenant, omr_code=omr_code).exists():
            raise serializers.ValidationError({"omr_code": "이미 사용 중인 OMR 식별자입니다."})

        return attrs
