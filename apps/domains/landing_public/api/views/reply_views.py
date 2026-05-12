"""댓글 ViewSet — 자유게시판 / 수강후기 공용 (polymorphic target)."""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndMember, TenantResolvedAndStaff

from ..serializers import (
    PublicPostReplySerializer,
    _is_staff_role,
    _resolve_display_name,
    _resolve_role,
)
from ...models import PublicBoardPost, PublicPostLike, PublicPostReply, PublicReview, PublicUserBlock


class PublicPostReplyViewSet(viewsets.GenericViewSet):
    """공용 댓글.

    list: 비로그인 OK. target 파라미터 필수 (target_kind:target_id).
    create: family only.
    update/destroy: 작성자 또는 staff.
    hide (staff): hidden toggle.
    """

    queryset = PublicPostReply.objects.all()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [TenantResolved()]
        if self.action == "hide":
            return [TenantResolvedAndStaff()]
        return [TenantResolvedAndMember()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicPostReply.objects.none()
        qs = PublicPostReply.objects.filter(tenant=tenant)
        # 숨김은 staff/작성자만
        user = self.request.user
        viewer_role = _resolve_role(user, tenant) if user.is_authenticated else ""
        if not _is_staff_role(viewer_role):
            from django.db.models import Q
            if user.is_authenticated:
                qs = qs.filter(Q(is_hidden=False) | Q(author=user))
            else:
                qs = qs.filter(is_hidden=False)
        return qs

    def list(self, request, *args, **kwargs):
        target = (request.query_params.get("target") or "").strip()
        if ":" not in target:
            return Response(
                {"detail": "target 파라미터 필수 (예: target=board:123 또는 target=review:45)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kind, _, raw_id = target.partition(":")
        if kind not in (PublicPostReply.TargetKind.BOARD, PublicPostReply.TargetKind.REVIEW):
            return Response({"detail": "target_kind는 board 또는 review."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_id = int(raw_id)
        except ValueError:
            return Response({"detail": "target_id가 잘못되었습니다."}, status=status.HTTP_400_BAD_REQUEST)
        qs = self.get_queryset().filter(target_kind=kind, target_id=target_id).order_by("created_at")
        ser = PublicPostReplySerializer(qs, many=True, context={"request": request})
        return Response({"results": ser.data, "count": qs.count()})

    def create(self, request, *args, **kwargs):
        kind = request.data.get("target_kind")
        target_id = request.data.get("target_id")
        if kind not in (PublicPostReply.TargetKind.BOARD, PublicPostReply.TargetKind.REVIEW):
            return Response({"detail": "target_kind 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return Response({"detail": "target_id 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        # 부모 존재 확인 (tenant 격리)
        tenant = request.tenant
        if kind == PublicPostReply.TargetKind.BOARD:
            parent_exists = PublicBoardPost.objects.filter(tenant=tenant, pk=target_id).exists()
        else:
            parent_exists = PublicReview.objects.filter(tenant=tenant, pk=target_id).exists()
        if not parent_exists:
            return Response({"detail": "대상이 존재하지 않습니다."}, status=status.HTTP_404_NOT_FOUND)
        # 블랙리스트 차단 (Phase 4-B)
        if PublicUserBlock.objects.filter(tenant=tenant, blocked_user=request.user).exists():
            return Response(
                {"detail": "댓글 작성이 제한된 사용자입니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        content = (request.data.get("content") or "").strip()
        if not content:
            return Response({"detail": "내용을 입력해주세요."}, status=status.HTTP_400_BAD_REQUEST)
        parent_reply_id = request.data.get("parent_reply")
        parent_reply = None
        if parent_reply_id:
            parent_reply = PublicPostReply.objects.filter(
                tenant=tenant, pk=parent_reply_id, target_kind=kind, target_id=target_id,
            ).first()
            if not parent_reply:
                return Response({"detail": "부모 댓글이 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        is_anonymous = bool(request.data.get("is_anonymous", False))
        user = request.user
        role = _resolve_role(user, tenant)
        is_owner = role == "owner"
        display = _resolve_display_name(user)
        obj = PublicPostReply.objects.create(
            tenant=tenant,
            target_kind=kind,
            target_id=target_id,
            author=user,
            author_display_name=display,
            author_role=role,
            is_anonymous=is_anonymous,
            is_owner_reply=is_owner,
            content=content,
            parent_reply=parent_reply,
        )
        return Response(
            PublicPostReplySerializer(obj, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if obj.author_id != user.id and not _is_staff_role(_resolve_role(user, request.tenant)):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="like")
    def like_toggle(self, request, pk=None):
        obj = self.get_object()
        user = request.user
        existing = PublicPostLike.objects.filter(
            user=user, target_kind=PublicPostLike.TargetKind.REPLY, target_id=obj.pk,
        ).first()
        if existing:
            existing.delete()
            obj.refresh_from_db(fields=["like_count"])
            return Response({"liked": False, "like_count": obj.like_count})
        PublicPostLike.objects.create(
            tenant=request.tenant,
            user=user,
            target_kind=PublicPostLike.TargetKind.REPLY,
            target_id=obj.pk,
        )
        obj.refresh_from_db(fields=["like_count"])
        return Response({"liked": True, "like_count": obj.like_count})

    @action(detail=True, methods=["post"], url_path="hide")
    def hide(self, request, pk=None):
        obj = self.get_object()
        obj.is_hidden = bool(request.data.get("is_hidden", True))
        obj.save(update_fields=["is_hidden", "updated_at"])
        return Response(PublicPostReplySerializer(obj, context={"request": request}).data)
