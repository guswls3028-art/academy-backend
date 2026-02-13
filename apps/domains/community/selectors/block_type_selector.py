from django.db.models import QuerySet

from apps.domains.community.models import BlockType


def get_block_types_for_tenant(tenant) -> QuerySet:
    """tenant별 BlockType 목록. order, id 순."""
    return BlockType.objects.filter(tenant=tenant).order_by("order", "id")


def get_empty_block_type_queryset() -> QuerySet:
    return BlockType.objects.none()
