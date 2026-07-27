from __future__ import annotations

from apps.domains.community.api.serializers import PostReplySerializer
from apps.domains.community.models import support_kind_for_post, support_subject


def _serialized_reply(reply) -> dict:
    data = dict(PostReplySerializer(reply).data)
    is_platform_reply = getattr(reply, "author_role", "") == "platform_staff"
    data["is_platform_reply"] = is_platform_reply
    data["can_delete"] = False
    return data


def support_ticket_status(post) -> str:
    replies = list(post.replies.all())
    latest_platform = max(
        (
            reply.created_at
            for reply in replies
            if getattr(reply, "author_role", "") == "platform_staff"
        ),
        default=None,
    )
    latest_requester = max(
        (
            reply.created_at
            for reply in replies
            if getattr(reply, "author_role", "") != "platform_staff"
        ),
        default=None,
    )
    if latest_platform is None:
        return "open"
    if latest_requester is not None and latest_requester > latest_platform:
        return "open"
    return "resolved"


def serialize_support_ticket(post) -> dict:
    kind = support_kind_for_post(post)
    if kind not in {"bug", "feedback"}:
        raise ValueError("Not a support ticket.")

    replies = list(post.replies.all())
    replies_data = [_serialized_reply(reply) for reply in replies]
    platform_replies = sum(
        1 for reply in replies if getattr(reply, "author_role", "") == "platform_staff"
    )
    attachments_data = [
        {
            "id": attachment.id,
            "original_name": attachment.original_name,
            "size_bytes": attachment.size_bytes,
            "content_type": attachment.content_type,
            "created_at": attachment.created_at.isoformat(),
        }
        for attachment in post.attachments.all()
    ]

    return {
        "source": "support",
        "id": post.id,
        "tenant_id": post.tenant_id,
        "tenant_code": post.tenant.code if post.tenant else None,
        "tenant_name": post.tenant.name if post.tenant else None,
        "title": post.title,
        "subject": support_subject(post.title),
        "content": post.content,
        "category_label": post.category_label,
        "author_display_name": post.author_display_name,
        "author_role": post.author_role,
        "created_at": post.created_at.isoformat(),
        "status": support_ticket_status(post),
        "replies_count": len(replies),
        "platform_replies_count": platform_replies,
        "replies": replies_data,
        "attachments": attachments_data,
        "inquiry_type": kind,
        "source_label": "버그 제보" if kind == "bug" else "개선 의견",
        "content_format": "sanitized_html",
        "contact_phone": None,
        "read_at": None,
        "admin_memo": "",
        "context": {},
    }
