# PATH: apps/domains/teachers/views.py
from rest_framework.viewsets import ModelViewSet
from .serializers import TeacherSerializer
from apps.core.permissions import TenantResolvedAndStaff
from academy.adapters.db.django import repositories_teachers as teacher_repo
from academy.adapters.db.django import repositories_staffs as staff_repo


class TeacherViewSet(ModelViewSet):
    serializer_class = TeacherSerializer
    permission_classes = [TenantResolvedAndStaff]

    def get_queryset(self):
        return teacher_repo.teacher_filter_tenant(self.request.tenant)

    def get_serializer_context(self):
        # list 액션의 TeacherSerializer.get_staff_id N+1 회피:
        # 테넌트 Staff의 (name, phone) → id 맵을 한 번에 로드해 직렬화 시 O(1) 룩업.
        ctx = super().get_serializer_context()
        if self.action == "list":
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                ctx["staff_id_map"] = staff_repo.staff_id_by_name_phone_map_tenant(tenant)
        return ctx

    def perform_create(self, serializer):
        # 🔐 tenant injection 방지: 항상 request.tenant 사용
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        # 🔐 tenant 변경 방지: update 시에도 tenant 고정
        serializer.save(tenant=self.request.tenant)
