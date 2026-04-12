import hashlib
import logging

from django.db import transaction
from apps.domains.community.services.html_sanitizer import sanitize_html
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.community.api.serializers import (
    PostEntitySerializer,
    PostReplySerializer,
    PostAttachmentSerializer,
    BlockTypeSerializer,
    ScopeNodeMinimalSerializer,
    PostTemplateSerializer,
)
from apps.domains.community.selectors import (
    get_posts_for_node,
    get_admin_post_list,
    get_post_by_id,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
    get_notice_posts_for_tenant,
    get_posts_by_type_for_tenant,
    get_block_types_for_tenant,
    get_empty_block_type_queryset,
    get_scope_nodes_for_tenant,
    get_empty_scope_node_queryset,
)
from apps.domains.community.services import CommunityService
from apps.domains.community.models import PostTemplate, PostReply, BlockType, PostAttachment
from apps.domains.student_app.permissions import get_request_student
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff, IsSuperuserOnly

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_ATTACHMENTS_PER_POST = 10


def _get_tenant_from_request(request):
    """request.tenant 반환. 테넌트 미해석 시 None (폴백 없음 — §B 절대 격리)."""
    return getattr(request, "tenant", None)
