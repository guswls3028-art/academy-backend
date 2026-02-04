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

        if len(phone) < 8 or phone[-8:] != omr_code:
            raise serializers.ValidationError(
                {"omr_code": "OMR 식별자는 전화번호 뒤 8자리와 일치해야 합니다."}
            )

        if Student.objects.filter(tenant=tenant, ps_number=ps_number).exists():
            raise serializers.ValidationError({"ps_number": "이미 사용 중인 PS 번호입니다."})

        if Student.objects.filter(tenant=tenant, omr_code=omr_code).exists():
            raise serializers.ValidationError({"omr_code": "이미 사용 중인 OMR 식별자입니다."})

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
        omr_code = attrs.get("omr_code", instance.omr_code)
        ps_number = attrs.get("ps_number", instance.ps_number)

        if phone and omr_code:
            if len(phone) < 8 or phone[-8:] != omr_code:
                raise serializers.ValidationError(
                    {"omr_code": "OMR 식별자는 전화번호 뒤 8자리와 일치해야 합니다."}
                )

        if ps_number:
            if Student.objects.filter(
                tenant=tenant, ps_number=ps_number
            ).exclude(id=instance.id).exists():
                raise serializers.ValidationError({"ps_number": "이미 사용 중인 PS 번호입니다."})

        if omr_code:
            if Student.objects.filter(
                tenant=tenant, omr_code=omr_code
            ).exclude(id=instance.id).exists():
                raise serializers.ValidationError({"omr_code": "이미 사용 중인 OMR 식별자입니다."})

        return attrs
