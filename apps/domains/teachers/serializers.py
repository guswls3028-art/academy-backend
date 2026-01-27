# PATH: apps/domains/teachers/serializers.py
from rest_framework import serializers
from .models import Teacher
from apps.domains.staffs.models import Staff


class TeacherSerializer(serializers.ModelSerializer):
    staff_id = serializers.SerializerMethodField()

    class Meta:
        model = Teacher
        fields = "__all__"

    def get_staff_id(self, obj):
        staff = Staff.objects.filter(
            name=obj.name,
            phone=obj.phone,
        ).first()
        return staff.id if staff else None
