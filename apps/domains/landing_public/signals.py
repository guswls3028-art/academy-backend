"""좋아요 / 댓글 카운트 캐시 갱신 signals.

PublicPostLike / PublicPostReply 변경 시 부모 모델의 `like_count` /
`reply_count` 를 동기 업데이트한다. atomic 안전성을 위해 F() expression 사용.
"""
from django.db import models, transaction
from django.db.models import F
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import PublicBoardPost, PublicPostLike, PublicPostReply, PublicReport, PublicReview


def _target_model(target_kind: str):
    if target_kind == PublicPostLike.TargetKind.BOARD:
        return PublicBoardPost
    if target_kind == PublicPostLike.TargetKind.REVIEW:
        return PublicReview
    if target_kind == PublicPostLike.TargetKind.REPLY:
        return PublicPostReply
    return None


# HOT 자동 뱃지 threshold — 좋아요 N개 이상이면 is_hot=True 자동 부여 (Board만).
# 학원장이 명시적으로 핀/HOT 끄지 않은 이상 자동 ON. staff 모더레이션 시 override 가능.
HOT_THRESHOLD = 5


@receiver(post_save, sender=PublicPostLike)
def _on_like_save(sender, instance: PublicPostLike, created, **kwargs):
    if not created:
        return
    Model = _target_model(instance.target_kind)
    if Model is None:
        return
    with transaction.atomic():
        Model.objects.filter(pk=instance.target_id).update(like_count=F("like_count") + 1)
        # HOT 자동 부여 — Board만(review/reply 제외). like_count 갱신 후 재조회로 정확도.
        if instance.target_kind == PublicPostLike.TargetKind.BOARD:
            obj = PublicBoardPost.objects.filter(pk=instance.target_id).only("like_count", "is_hot").first()
            if obj and (obj.like_count or 0) >= HOT_THRESHOLD and not obj.is_hot:
                PublicBoardPost.objects.filter(pk=instance.target_id).update(is_hot=True)


@receiver(post_delete, sender=PublicPostLike)
def _on_like_delete(sender, instance: PublicPostLike, **kwargs):
    Model = _target_model(instance.target_kind)
    if Model is None:
        return
    # like_count 가 음수가 되지 않도록 max(0, ...) 가드
    with transaction.atomic():
        obj = Model.objects.filter(pk=instance.target_id).only("like_count").first()
        if not obj:
            return
        new_count = max(0, (obj.like_count or 0) - 1)
        Model.objects.filter(pk=instance.target_id).update(like_count=new_count)


@receiver(post_save, sender=PublicPostReply)
def _on_reply_save(sender, instance: PublicPostReply, created, **kwargs):
    if not created:
        return
    Model = _target_model(instance.target_kind)
    if Model is None or Model is PublicPostReply:
        return
    # 대댓글(parent_reply 존재)도 부모 게시글의 reply_count에 반영 (열린 정책 — 차후 분리 가능)
    with transaction.atomic():
        Model.objects.filter(pk=instance.target_id).update(reply_count=F("reply_count") + 1)


@receiver(post_delete, sender=PublicPostReply)
def _on_reply_delete(sender, instance: PublicPostReply, **kwargs):
    Model = _target_model(instance.target_kind)
    if Model is None or Model is PublicPostReply:
        return
    with transaction.atomic():
        obj = Model.objects.filter(pk=instance.target_id).only("reply_count").first()
        if not obj:
            return
        new_count = max(0, (obj.reply_count or 0) - 1)
        Model.objects.filter(pk=instance.target_id).update(reply_count=new_count)


# Phase 5-B: 신고 누적 자동 hide threshold.
# Why: 학원장이 inbox 들어오기 전 악성 컨텐츠 노출 시간 최소화. 3명 이상이 동일 글에 pending 신고
# 누적이면 자동 hidden 처리 → 학원장 inbox에서 검토 후 복원/유지 결정.
# How to apply: PublicReport post_save 시 동일 (tenant, target_kind, target_id) pending 신고 count
# 가 threshold 이상이면 대상 모델 status를 hidden(board/review) 또는 is_hidden=True(reply)로 갱신.
AUTO_HIDE_THRESHOLD = 3


@receiver(post_save, sender=PublicReport)
def _on_report_save_autohide(sender, instance: PublicReport, created, **kwargs):
    if not created or instance.status != PublicReport.Status.PENDING:
        return
    pending_count = PublicReport.objects.filter(
        tenant=instance.tenant,
        target_kind=instance.target_kind,
        target_id=instance.target_id,
        status=PublicReport.Status.PENDING,
    ).count()
    if pending_count < AUTO_HIDE_THRESHOLD:
        return
    # 대상별 자동 hide. 이미 hidden 상태면 noop.
    if instance.target_kind == PublicReport.TargetKind.BOARD:
        PublicBoardPost.objects.filter(
            tenant=instance.tenant, pk=instance.target_id, status=PublicBoardPost.Status.PUBLISHED,
        ).update(status=PublicBoardPost.Status.HIDDEN)
    elif instance.target_kind == PublicReport.TargetKind.REVIEW:
        PublicReview.objects.filter(
            tenant=instance.tenant, pk=instance.target_id, status=PublicReview.Status.APPROVED,
        ).update(status=PublicReview.Status.HIDDEN)
    elif instance.target_kind == PublicReport.TargetKind.REPLY:
        PublicPostReply.objects.filter(
            tenant=instance.tenant, pk=instance.target_id, is_hidden=False,
        ).update(is_hidden=True)


# Phase 4-A: verified 자동 — PublicReview 등록 시 author가 학원 family 활성 member면 ✓ 수강 인증.
# Why: 외부 학부모 신뢰 신호 = 진짜 수강생/학부모만 후기 작성. staff manual toggle 위에 자동 baseline.
# How: post_save 시 author.tenant_id == review.tenant_id OR TenantMembership active 존재 → is_verified=True.
@receiver(post_save, sender=PublicReview)
def _on_review_save_verify(sender, instance: PublicReview, created, **kwargs):
    if not created:
        return
    if instance.is_verified:
        return  # staff가 미리 True로 설정한 케이스(직접 insert) 우회
    author = instance.author
    if not author or not author.is_authenticated and not author.id:
        return
    # 2026-05-13 보완: tenant_id 일치만으로는 약함 (퇴원 후 작성 가능). active membership 필수.
    # User.tenant FK는 학생/학부모 default tenant 표시일 뿐 — 실제 active 학원 family 여부는
    # TenantMembership.is_active=True 또는 Student/Parent 도메인 active 상태로 확인.
    try:
        from academy.adapters.db.django import repositories_core as core_repo
        is_active_member = core_repo.membership_exists(tenant=instance.tenant, user=author, is_active=True)
        if is_active_member:
            PublicReview.objects.filter(pk=instance.pk).update(is_verified=True)
            return
        # Fallback: User.tenant_id 일치 AND user.is_active (membership table 없는 학생 케이스)
        if (
            getattr(author, "tenant_id", None) == instance.tenant_id
            and getattr(author, "is_active", False)
        ):
            PublicReview.objects.filter(pk=instance.pk).update(is_verified=True)
    except Exception:
        # 인증 자동 부여 실패는 silent (staff가 수동 verify 가능). 후기 등록 자체 막지 않음.
        pass
