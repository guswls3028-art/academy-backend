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
        # ViewSet.list가 (name, phone) → id 맵을 컨텍스트로 주입하면 O(1) 룩업으로 N+1 회피.
        staff_id_map = self.context.get("staff_id_map")
        if staff_id_map is not None:
            return staff_id_map.get((obj.name, obj.phone or ""))
        # 컨텍스트가 없는 경우(단독 사용): 안전한 폴백.
        tenant = getattr(obj, "tenant", None)
        if not tenant:
            return None
        staff = staff_repo.staff_get_by_name_phone(obj.name, obj.phone or "", tenant=tenant)
        return staff.id if staff else None
