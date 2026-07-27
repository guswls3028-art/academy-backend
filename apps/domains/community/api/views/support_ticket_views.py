from __future__ import annotations

from collections.abc import Mapping

from django.db.models import Prefetch
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.community.api.support_ticket_contract import (
    serialize_support_ticket,
)
from apps.domains.community.models import (
    PostAttachment,
    PostEntity,
    PostReply,
    platform_support_q,
    support_kind_for_post,
)
from apps.domains.community.services.html_sanitizer import sanitize_html
from ._common import normalize_idempotency_key


def _support_queryset():
    return (
        PostEntity.objects.filter(post_type="board")
        .filter(platform_support_q())
        .select_related("tenant", "created_by")
        .prefetch_related(
            Prefetch(
                "replies",
                queryset=PostReply.objects.select_related("created_by").order_by("created_at"),
            ),
            Prefetch("attachments", queryset=PostAttachment.objects.order_by("created_at")),
        )
        .order_by("-created_at")
    )


def _staff_display_name(user) -> str:
    staff = getattr(user, "staff", None) or getattr(user, "staff_profile", None)
    if staff and getattr(staff, "name", None):
        return str(staff.name)[:100]
    full_name = f"{getattr(user, 'last_name', '')}{getattr(user, 'first_name', '')}".strip()
    return full_name[:100] or (getattr(user, "username", "") or "관리자")[:100]


class SupportTicketListCreateView(APIView):
    """테넌트 staff가 개발자에게 보내는 비공개 버그/피드백."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        ticket_type = (request.query_params.get("type") or "all").strip().lower()
        if ticket_type not in {"all", "bug", "feedback"}:
            return Response(
                {"detail": "type은 all, bug, feedback 중 하나여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = _support_queryset().filter(tenant=request.tenant)
        if ticket_type != "all":
            explicit = qs.filter(support_kind=ticket_type)
            legacy = qs.filter(support_kind__isnull=True)
            qs = explicit | legacy
            qs = [
                post for post in qs.order_by("-created_at")
                if support_kind_for_post(post) == ticket_type
            ]
        else:
            qs = list(qs[:200])

        results = [serialize_support_ticket(post) for post in qs[:200]]
        response = Response({"results": results, "count": len(results)})
        response["Cache-Control"] = "private, no-store"
        return response

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return Response(
                {"detail": "요청 본문은 JSON 객체여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ticket_type = str(request.data.get("type") or "").strip().lower()
        subject = str(request.data.get("subject") or "").strip()
        content = str(request.data.get("content") or "").strip()
        try:
            request_key = normalize_idempotency_key(
                request.data.get("idempotency_key")
            )
        except ValueError:
            return Response(
                {"detail": "문의 재시도 키 형식이 올바르지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if ticket_type not in {"bug", "feedback"}:
            return Response(
                {"detail": "type은 bug 또는 feedback이어야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not subject:
            return Response(
                {"detail": "제목을 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(subject) > 200:
            return Response(
                {"detail": "제목은 200자 이내여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(content) > 10_000:
            return Response(
                {"detail": "상세 내용은 10,000자 이내여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        prefix = "[BUG]" if ticket_type == "bug" else "[FB]"
        title = f"{prefix} {subject}"
        sanitized_content = sanitize_html(content)
        defaults = {
            "post_type": "board",
            "support_kind": ticket_type,
            "title": title,
            "content": sanitized_content,
            "author_display_name": _staff_display_name(request.user),
            "author_role": "staff",
            "status": "published",
        }
        created = True
        if request_key:
            post, created = PostEntity.objects.get_or_create(
                tenant=request.tenant,
                support_request_key=request_key,
                defaults=defaults,
            )
            if not created and (
                post.support_kind != ticket_type
                or post.title != title
                or post.content != sanitized_content
            ):
                return Response(
                    {"detail": "같은 문의 재시도 키가 다른 내용에 사용되었습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            post = PostEntity.objects.create(
                tenant=request.tenant,
                support_request_key=None,
                **defaults,
            )
        post = _support_queryset().get(pk=post.pk)
        return Response(
            serialize_support_ticket(post),
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )
