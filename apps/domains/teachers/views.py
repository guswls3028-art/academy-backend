# PATH: apps/domains/teachers/views.py
from rest_framework.viewsets import ModelViewSet
from .serializers import TeacherSerializer
from apps.core.permissions import TenantResolvedAndStaff
from academy.adapters.db.django import repositories_teachers as teacher_repo


class TeacherViewSet(ModelViewSet):
    serializer_class = TeacherSerializer
    permission_classes = [TenantResolvedAndStaff]

    def get_queryset(self):
        return teacher_repo.teacher_filter_tenant(self.request.tenant)

    def perform_create(self, serializer):
        # 🔐 tenant injection 방지: 항상 request.tenant 사용
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        # 🔐 tenant 변경 방지: update 시에도 tenant 고정
        serializer.save(tenant=self.request.tenant)
