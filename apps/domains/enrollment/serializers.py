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
            "elementary_school",
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
        fields = [
            "id", "tenant", "student", "lecture", "status",
            "enrolled_at", "created_at", "updated_at",
        ]


class SessionEnrollmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )
    student_id = serializers.IntegerField(
        source="enrollment.student_id", read_only=True
    )
    # 학원장이 "직전 차시 불러오기" 명단에서 동명이인 식별할 수 있도록 학교·학년 노출.
    # school은 school_type 기반으로 high/middle/elementary 중 하나를 합성 (frontend mapStudent와 동일 규칙).
    student_grade = serializers.IntegerField(
        source="enrollment.student.grade", read_only=True, allow_null=True
    )
    student_school = serializers.SerializerMethodField()

    class Meta:
        model = SessionEnrollment
        fields = [
            "id", "tenant", "session", "enrollment",
            "student_name", "student_id", "student_school", "student_grade",
            "created_at",
        ]

    def get_student_school(self, obj):
        student = getattr(getattr(obj, "enrollment", None), "student", None)
        if student is None:
            return None
        return (
            getattr(student, "high_school", None)
            or getattr(student, "middle_school", None)
            or getattr(student, "elementary_school", None)
            or None
        )
