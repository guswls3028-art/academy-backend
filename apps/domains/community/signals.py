"""Lecture/Session 저장 시 ScopeNode 자동 생성.

✅ V1.1.1: created=True 제한 제거.
get_or_create이므로 매번 호출해도 안전하며,
기존 Lecture/Session에 ScopeNode가 누락된 경우를 자동 복구한다.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.domains.community.services.scope_node_service import (
    ensure_scope_node_for_lecture,
    ensure_scope_node_for_session,
)


@receiver(post_save, sender="lectures.Lecture")
def on_lecture_saved(sender, instance, **kwargs):
    ensure_scope_node_for_lecture(instance)


@receiver(post_save, sender="lectures.Session")
def on_session_saved(sender, instance, **kwargs):
    ensure_scope_node_for_session(instance)


# ─── 커뮤니티 알림 signal (#62 N, 2026-05-12) ───
# PostReply / PostLike / PostReplyLike create 시 글 작성자에게 CommunityNotification 생성.
# self-action은 skip(자기 글 댓글/좋아요 무의미).
# Student.user OneToOne으로 학생 recipient 매핑. staff 작성자(created_by=null)는 skip — 학원장 알림은 admin_notifications 별도.

from apps.domains.community.models import PostReply, PostLike, PostReplyLike, CommunityNotification


def _safe_get_user_id(student) -> int | None:
    if student is None:
        return None
    try:
        return getattr(student, "user_id", None)
    except Exception:
        return None


@receiver(post_save, sender=PostReply)
def on_reply_created_notify(sender, instance: PostReply, created: bool, **kwargs):
    if not created:
        return
    try:
        if instance.parent_reply_id:
            # 답글 — 부모 댓글 작성자에게 알림
            try:
                parent = PostReply.objects.select_related("created_by").get(id=instance.parent_reply_id)
            except PostReply.DoesNotExist:
                return
            recipient_user_id = _safe_get_user_id(parent.created_by)
            actor_user_id = _safe_get_user_id(instance.created_by)
            if not recipient_user_id or recipient_user_id == actor_user_id:
                return
            CommunityNotification.objects.create(
                tenant_id=instance.tenant_id,
                recipient_id=recipient_user_id,
                kind=CommunityNotification.KIND_CHILD_REPLY,
                payload={
                    "post_id": instance.post_id,
                    "reply_id": instance.id,
                    "parent_reply_id": instance.parent_reply_id,
                    "actor_user_id": actor_user_id,
                    "actor_name": getattr(instance.created_by, "name", None),
                },
            )
            return
        # 일반 댓글 — 글 작성자에게 알림
        post = instance.post
        recipient_user_id = _safe_get_user_id(post.created_by)
        actor_user_id = _safe_get_user_id(instance.created_by)
        if not recipient_user_id or recipient_user_id == actor_user_id:
            return
        CommunityNotification.objects.create(
            tenant_id=instance.tenant_id,
            recipient_id=recipient_user_id,
            kind=CommunityNotification.KIND_POST_REPLY,
            payload={
                "post_id": instance.post_id,
                "reply_id": instance.id,
                "actor_user_id": actor_user_id,
                "actor_name": getattr(instance.created_by, "name", None),
            },
        )
    except Exception:
        # 알림 실패가 reply 저장을 막아선 안 됨 (best-effort)
        pass


@receiver(post_save, sender=PostLike)
def on_post_like_notify(sender, instance: PostLike, created: bool, **kwargs):
    if not created:
        return
    try:
        post = instance.post
        recipient_user_id = _safe_get_user_id(post.created_by)
        actor_user_id = instance.user_id
        if not recipient_user_id or recipient_user_id == actor_user_id:
            return
        CommunityNotification.objects.create(
            tenant_id=instance.tenant_id,
            recipient_id=recipient_user_id,
            kind=CommunityNotification.KIND_POST_LIKE,
            payload={
                "post_id": instance.post_id,
                "actor_user_id": actor_user_id,
            },
        )
    except Exception:
        pass


@receiver(post_save, sender=PostReplyLike)
def on_reply_like_notify(sender, instance: PostReplyLike, created: bool, **kwargs):
    if not created:
        return
    try:
        reply = instance.reply
        recipient_user_id = _safe_get_user_id(reply.created_by)
        actor_user_id = instance.user_id
        if not recipient_user_id or recipient_user_id == actor_user_id:
            return
        CommunityNotification.objects.create(
            tenant_id=instance.tenant_id,
            recipient_id=recipient_user_id,
            kind=CommunityNotification.KIND_REPLY_LIKE,
            payload={
                "post_id": reply.post_id,
                "reply_id": reply.id,
                "actor_user_id": actor_user_id,
            },
        )
    except Exception:
        pass
