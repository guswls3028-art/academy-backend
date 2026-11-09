"""Exact community metadata deletion and durable, post-commit object cleanup."""

from django.db.models import Q

from apps.domains.community.models import PostAttachment, PostReplyAttachment
from apps.domains.community.services.attachment_urls import reply_image_namespace
from apps.support.community.storage_cleanup_dependencies import (
    lock_storage_object_keys,
    schedule_detached_storage_cleanup,
    storage_cleanup_status,
)


class CommunityDeleteScopeError(ValueError):
    """The attachment graph or object namespace differs from its owning post."""


def _reply_attachment_keys(*, post, reply=None):
    images = PostReplyAttachment.objects.select_related("reply")
    if reply is None:
        images = images.filter(reply__post=post)
    else:
        images = images.filter(Q(reply=reply) | Q(reply__parent_reply=reply))

    prefix = reply_image_namespace(post.tenant_id, post.id)
    keys = []
    for image in images:
        if image.reply.tenant_id != post.tenant_id or image.reply.post_id != post.id or not image.r2_key.startswith(prefix):
            raise CommunityDeleteScopeError("reply attachment does not match its post")
        keys.append(image.r2_key)
    return keys


def delete_reply_content(*, post, reply):
    """Caller holds the post lock used for reply image upload."""
    keys = lock_storage_object_keys(
        tenant_id=post.tenant_id, object_keys=_reply_attachment_keys(post=post, reply=reply),
    )
    reply.delete()
    intent_ids = schedule_detached_storage_cleanup(tenant_id=post.tenant_id, object_keys=keys)
    return {"posts": 0, "attachments": len(keys)}, intent_ids


def delete_post_content(*, post, attachment=None):
    """Caller holds the same post row lock as upload and has checked permissions."""
    attachments = [attachment] if attachment is not None else list(
        PostAttachment.objects.select_for_update().filter(post=post).order_by("id")
    )
    if any(att.tenant_id != post.tenant_id or att.post_id != post.id for att in attachments):
        raise CommunityDeleteScopeError("attachment tenant does not match its post")
    reply_keys = _reply_attachment_keys(post=post) if attachment is None else []
    try:
        keys = lock_storage_object_keys(
            tenant_id=post.tenant_id, object_keys=[att.r2_key for att in attachments] + reply_keys,
        )
    except ValueError as exc:
        raise CommunityDeleteScopeError("attachment object namespace differs from its tenant") from exc
    if attachment is None:
        post.delete()
    else:
        attachment.delete()
    intent_ids = schedule_detached_storage_cleanup(tenant_id=post.tenant_id, object_keys=keys)
    return {"posts": int(attachment is None), "attachments": len(attachments) + len(reply_keys)}, intent_ids


def deletion_result(*, tenant_id, deleted, intent_ids):
    cleanup = storage_cleanup_status(tenant_id=tenant_id, intent_ids=intent_ids)
    result = {
        "deleted": {**deleted, "r2_objects": cleanup["cleaned"]},
        "storage_cleanup": cleanup,
    }
    if cleanup["pending"] or cleanup["failed"]:
        result.update(
            code="community_storage_cleanup_pending",
            detail="목록에서 삭제되었습니다. 원본 파일 정리는 재시도 대기 중입니다.",
        )
    return result
