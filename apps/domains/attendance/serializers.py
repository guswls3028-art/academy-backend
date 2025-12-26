from rest_framework import serializers
from .models import Attendance


class AttendanceSerializer(serializers.ModelSerializer):
    name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )
    parent_phone = serializers.CharField(
        source="enrollment.student.parent_phone", read_only=True
    )
    phone = serializers.CharField(
        source="enrollment.student.phone", read_only=True
    )

    class Meta:
        model = Attendance
        fields = [
            "id",
            "session",
            "enrollment_id",  # ⭐ 추가
            "status",
            "memo",
            "name",
            "parent_phone",
            "phone",
        ]
