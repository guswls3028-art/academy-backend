from rest_framework import serializers

from .models import Enrollment, SessionEnrollment
from apps.domains.students.models import Student


class StudentShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = [
            "id",
            "name",
            "grade",
            "high_school",
            "high_school_class",
            "major",
            "phone",
            "parent_phone",
        ]


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
