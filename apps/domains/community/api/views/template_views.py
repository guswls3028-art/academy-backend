from rest_framework import viewsets

from apps.domains.community.api.serializers import PostTemplateSerializer
from apps.domains.community.models import PostTemplate
from apps.core.permissions import TenantResolvedAndStaff


class PostTemplateViewSet(viewsets.ModelViewSet):
    """글 양식 CRUD. 자주 쓰는 제목/본문/유형 저장·불러오기."""
    serializer_class = PostTemplateSerializer
    permission_classes = [TenantResolvedAndStaff]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PostTemplate.objects.none()
        return (
            PostTemplate.objects.filter(tenant=tenant)
            .select_related("block_type")
            .order_by("order", "id")
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            serializer.save(tenant=tenant)
