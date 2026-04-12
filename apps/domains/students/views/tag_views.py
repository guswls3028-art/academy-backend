# PATH: apps/domains/students/views/tag_views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff

from academy.adapters.db.django import repositories_students as student_repo
from ..serializers import TagSerializer


# ======================================================
# Tag
# ======================================================

class TagViewSet(ModelViewSet):
    """
    학생 태그 관리
    - 관리자 / 스태프 전용
    - 테넌트별 격리
    """
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_queryset(self):
        return student_repo.tag_all(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)
