# apps/domains/attendance/serializers.py

from rest_framework import serializers
from .models import Attendance


class AttendanceSerializer(serializers.ModelSerializer):
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

    class Meta:
        model = Attendance
        fields = [
            "id",
            "session",
            "enrollment_id",
            "status",
            "memo",
            "name",
            "parent_phone",
            "phone",
        ]
        
class AttendanceMatrixStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField(allow_null=True)
    parent_phone = serializers.CharField(allow_null=True)
    attendance = serializers.DictField()
