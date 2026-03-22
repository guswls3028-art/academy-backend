# PATH: apps/domains/teachers/serializers.py
from rest_framework import serializers
from .models import Teacher
from academy.adapters.db.django import repositories_staffs as staff_repo


class TeacherSerializer(serializers.ModelSerializer):
    staff_id = serializers.SerializerMethodField()

    class Meta:
        model = Teacher
        fields = [
            "id", "tenant", "name", "phone", "email",
            "subject", "note", "is_active",
            "created_at", "updated_at", "staff_id",
        ]
        read_only_fields = ["id", "tenant", "created_at", "updated_at", "staff_id"]

    def get_staff_id(self, obj):
        # 🔐 tenant-scoped lookup: 같은 테넌트 내에서만 Staff 매칭
        tenant = getattr(obj, "tenant", None)
        if not tenant:
            return None
        staff = staff_repo.staff_get_by_name_phone(obj.name, obj.phone or "", tenant=tenant)
        return staff.id if staff else None
