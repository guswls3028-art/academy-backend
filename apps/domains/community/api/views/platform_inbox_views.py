import logging

from apps.domains.community.services.html_sanitizer import sanitize_html
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.community.api.serializers import (
    PostReplySerializer,
    PostAttachmentSerializer,
)
from apps.domains.community.models import PostReply
from apps.domains.community.models.post import PostEntity
from apps.domains.community.models import PostAttachment
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import IsSuperuserOnly
from apps.core.services.ops_audit import record_audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# Platform Inbox — 크로스테넌트 버그/피드백 수신함 (Superuser only)
# ═══════════════════════════════════════════════════════

class PlatformInboxListView(APIView):
    """
    GET /api/v1/community/platform/inbox/
    전체 테넌트의 [BUG]/[FB] 게시글 목록 (superuser 전용).
    ?type=bug|feedback|all (default: all)
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def get(self, request):
        from django.db.models import Count, Prefetch
        from apps.core.models import Tenant

        post_type_filter = (request.query_params.get("type") or "all").strip().lower()

        qs = PostEntity.objects.filter(
            post_type="board",
        ).select_related("tenant", "created_by").prefetch_related(
            "attachments",
            Prefetch(
                "replies",
                queryset=PostReply.objects.select_related("created_by").order_by("created_at"),
            ),
        ).annotate(
            _replies_count=Count("replies"),
        ).order_by("-created_at")

        if post_type_filter == "bug":
            qs = qs.filter(title__startswith="[BUG]")
        elif post_type_filter == "feedback":
            qs = qs.filter(title__startswith="[FB]")
        else:
            qs = qs.filter(Q(title__startswith="[BUG]") | Q(title__startswith="[FB]"))

        results = []
        for post in qs[:500]:
            replies_data = PostReplySerializer(post.replies.all(), many=True).data
            attachments_data = PostAttachmentSerializer(post.attachments.all(), many=True).data
            results.append({
                "id": post.id,
                "tenant_id": post.tenant_id,
                "tenant_code": post.tenant.code if post.tenant else None,
                "tenant_name": post.tenant.name if post.tenant else None,
                "title": post.title,
                "content": post.content,
                "category_label": post.category_label,
                "author_display_name": post.author_display_name,
                "author_role": post.author_role,
                "created_at": post.created_at.isoformat(),
                "replies_count": post._replies_count,
                "replies": replies_data,
                "attachments": attachments_data,
                "inquiry_type": "bug" if post.title.startswith("[BUG]") else "feedback",
            })

        return Response({"results": results, "count": len(results)})


class PlatformInboxReplyView(APIView):
    """
    POST /api/v1/community/platform/inbox/<post_id>/replies/
    크로스테넌트 문의에 개발자 답변 등록 (superuser 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def post(self, request, post_id):
        try:
            post = PostEntity.objects.get(pk=post_id)
        except PostEntity.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        content = (request.data.get("content") or "").strip()
        if not content:
            return Response({"detail": "content is required."}, status=status.HTTP_400_BAD_REQUEST)

        content = sanitize_html(content)

        # Resolve author display name
        author_display_name = "개발자"
        staff = getattr(request.user, "staff", None) or getattr(request.user, "staff_profile", None)
        if staff and getattr(staff, "name", None):
            author_display_name = staff.name

        reply = PostReply.objects.create(
            post=post,
            tenant_id=post.tenant_id,
            content=content,
            created_by=None,
            author_display_name=author_display_name,
            author_role="staff",
        )

        logger.info(
            "Platform inbox reply: post=%s tenant=%s by=%s",
            post.id, post.tenant_id, request.user.pk,
        )

        record_audit(
            request,
            action="inbox.reply",
            target_tenant=getattr(post, "tenant", None),
            summary=f"Inbox reply on post#{post.id} ({post.title[:40]})",
            payload={"post_id": post.id, "reply_id": reply.id},
        )
        return Response(PostReplySerializer(reply).data, status=status.HTTP_201_CREATED)


class PlatformInboxDeleteReplyView(APIView):
    """
    DELETE /api/v1/community/platform/inbox/<post_id>/replies/<reply_id>/
    개발자 답변 삭제 (superuser 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def delete(self, request, post_id, reply_id):
        try:
            reply = PostReply.objects.select_related("post", "post__tenant").get(
                pk=reply_id, post_id=post_id,
            )
        except PostReply.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        target_tenant = getattr(getattr(reply, "post", None), "tenant", None)
        reply.delete()
        record_audit(
            request,
            action="inbox.reply_delete",
            target_tenant=target_tenant,
            summary=f"Inbox reply deleted: post#{post_id}, reply#{reply_id}",
            payload={"post_id": post_id, "reply_id": reply_id},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlatformInboxAttachmentDownloadView(APIView):
    """
    GET /api/v1/community/platform/inbox/<post_id>/attachments/<att_id>/download/
    크로스테넌트 첨부 다운로드 presigned URL (superuser 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def get(self, request, post_id, att_id):
        try:
            att = PostAttachment.objects.select_related("post").get(id=att_id, post_id=post_id)
        except PostAttachment.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # 인박스 글(BUG/FB)에 한해 허용
        title = (att.post.title or "")
        if not (title.startswith("[BUG]") or title.startswith("[FB]")):
            return Response({"detail": "Not an inbox attachment."}, status=status.HTTP_404_NOT_FOUND)

        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
        url = generate_presigned_get_url_storage(
            key=att.r2_key,
            expires_in=3600,
            filename=att.original_name,
            content_type=att.content_type or None,
        )
        return Response({"url": url, "original_name": att.original_name})
