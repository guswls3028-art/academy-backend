
from rest_framework import serializers


from apps.domains.students.models import Student, Tag
from apps.domains.enrollment.models import Enrollment
from apps.domains.interactions.counseling.models import Counseling
from apps.domains.interactions.questions.models import Question


# -------------------------------
# Tag
# -------------------------------

class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = "__all__"
        ref_name = "StudentTagSerializer"


# -------------------------------
# Nested
# -------------------------------

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


# -------------------------------
# Student
# -------------------------------

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


# -------------------------------
# Student Create (with User)
# -------------------------------

class StudentCreateSerializer(serializers.ModelSerializer):
    """
    학생 생성 전용 Serializer
    - 교사가 초기 비밀번호 설정
    - phone → User.username 으로 사용
    """

    initial_password = serializers.CharField(
        write_only=True,
        required=True,
        min_length=4,
        help_text="학생 초기 비밀번호 (교사 설정)",
    )

    class Meta:
        model = Student
        fields = "__all__"

    def validate_phone(self, value):
        if not value:
            raise serializers.ValidationError("전화번호는 필수입니다.")

        from django.contrib.auth import get_user_model
        User = get_user_model()

        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("이미 사용 중인 전화번호입니다.")

        return value
