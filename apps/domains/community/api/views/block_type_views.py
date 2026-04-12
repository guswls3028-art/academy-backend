from rest_framework import viewsets

from apps.domains.community.api.serializers import BlockTypeSerializer
from apps.domains.community.selectors import (
    get_block_types_for_tenant,
    get_empty_block_type_queryset,
)
from apps.domains.community.models import BlockType
from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff


class BlockTypeViewSet(viewsets.ModelViewSet):
    """블록 유형 CRUD. 커스텀 유형 생성/수정/삭제. tenant에 하나도 없으면 기본 QnA 유형 자동 생성."""
    serializer_class = BlockTypeSerializer

    def get_permissions(self):
        # 목록/상세 조회는 학생도 가능 (QnA block type ID resolve 용)
        if self.action in ("list", "retrieve"):
            return [TenantResolvedAndMember()]
        return [TenantResolvedAndStaff()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_block_type_queryset()
        return get_block_types_for_tenant(tenant)

    # 기본 블록 유형 (tenant에 하나도 없을 때 자동 생성)
    _DEFAULT_BLOCK_TYPES = [
        ("qna", "QnA", 1),
        ("notice", "공지", 2),
        ("counsel", "상담 신청", 50),
        ("materials", "자료실", 60),
    ]

    def list(self, request, *args, **kwargs):
        """목록 조회 시 tenant에 블록 유형이 없으면 기본 유형을 한 번만 생성 후 반환."""
        tenant = getattr(request, "tenant", None)
        if tenant:
            qs = get_block_types_for_tenant(tenant)
            if not qs.exists():
                for code, label, order in self._DEFAULT_BLOCK_TYPES:
                    BlockType.objects.get_or_create(
                        tenant=tenant,
                        code=code,
                        defaults={"label": label, "order": order},
                    )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        data = dict(serializer.validated_data)
        code = (data.pop("code", None) or "").strip()[:32]
        if not code:
            import re
            code = re.sub(r"[^a-zA-Z0-9가-힣_]", "_", data.get("label", ""))[:32] or "CUSTOM"
        serializer.save(tenant=tenant, code=code, **data)
