# PATH: apps/domains/teachers/serializers.py
from rest_framework import serializers
from .models import Teacher
from academy.adapters.db.django import repositories_staffs as staff_repo


class TeacherSerializer(serializers.ModelSerializer):
    staff_id = serializers.SerializerMethodField()

    class Meta:
        model = Teacher
        fields = "__all__"

    def get_staff_id(self, obj):
        staff = staff_repo.staff_get_by_name_phone(obj.name, obj.phone or "")
        return staff.id if staff else None
