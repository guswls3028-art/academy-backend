from rest_framework import serializers

from .models import Enrollment, SessionEnrollment
from apps.domains.students.models import Student


class StudentShortSerializer(serializers.ModelSerializer):
    """강의/수강 맥락에서 노출하는 학생 최소 정보. Student 모델 스펙과 정합성 유지."""

    class Meta:
        model = Student
        fields = [
            "id",
            "name",
            "grade",
            "school_type",
            "high_school",
            "middle_school",
            "high_school_class",
            "major",
            "origin_middle_school",
            "phone",
            "parent_phone",
        ]
        extra_kwargs = {"phone": {"allow_null": True}}


class EnrollmentSerializer(serializers.ModelSerializer):
    student = StudentShortSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = "__all__"


class SessionEnrollmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )
    student_id = serializers.IntegerField(
        source="enrollment.student_id", read_only=True
    )

    class Meta:
        model = SessionEnrollment
        fields = "__all__"
