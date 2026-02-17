# apps/domains/attendance/serializers.py

from rest_framework import serializers
from .models import Attendance


class AttendanceSerializer(serializers.ModelSerializer):
    student_id = serializers.IntegerField(
        source="enrollment.student_id",
        read_only=True,
    )
    name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )
    parent_phone = serializers.CharField(
        source="enrollment.student.parent_phone",
        read_only=True,
    )
    phone = serializers.CharField(
        source="enrollment.student.phone",
        read_only=True,
    )
    lecture_title = serializers.CharField(
        source="session.lecture.title",
        read_only=True,
    )
    lecture_color = serializers.CharField(
        source="session.lecture.color",
        read_only=True,
        default="#3b82f6",
    )

    class Meta:
        model = Attendance
        fields = [
            "id",
            "session",
            "enrollment_id",
            "student_id",
            "status",
            "memo",
            "name",
            "parent_phone",
            "phone",
            "lecture_title",
            "lecture_color",
        ]
        
class AttendanceMatrixStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField(allow_null=True)
    parent_phone = serializers.CharField(allow_null=True)
    attendance = serializers.DictField()
