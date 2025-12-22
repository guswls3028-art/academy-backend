from rest_framework import serializers
from .models import Counseling


class CounselingSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )

    class Meta:
        model = Counseling
        fields = "__all__"
