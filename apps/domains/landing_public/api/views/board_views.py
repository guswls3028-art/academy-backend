"""자유게시판 ViewSet — 외부 공개 list/detail + family write + staff moderate."""
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndMember, TenantResolvedAndStaff

from ..serializers import (
    PublicBoardPostDetailSerializer,
    PublicBoardPostListSerializer,
    PublicBoardPostWriteSerializer,
    _is_staff_role,
    _resolve_display_name,
    _resolve_role,
)
from ...models import PublicBoardPost, PublicPostLike, PublicUserBlock
from ...services.matchup_guard import filter_allowed_report_ids


class PublicBoardPostViewSet(viewsets.GenericViewSet):
    """공개 자유게시판.

    list / retrieve: 비로그인 OK (`external_visible=True` + `status=published`)
    create: 학원 family 로그인 필요
    update / destroy: 작성자 본인 또는 staff
    moderate (pin/hot/hide/external_visible toggle): staff only
    like (toggle): 학원 family 로그인 필요
    """

    queryset = PublicBoardPost.objects.all()
    lookup_field = "pk"

    # ─── Permissions per action ───

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [TenantResolved()]
        if self.action == "moderate":
            return [TenantResolvedAndStaff()]
        return [TenantResolvedAndMember()]

    # ─── Queryset ───

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicBoardPost.objects.none()
        qs = PublicBoardPost.objects.filter(tenant=tenant).exclude(status=PublicBoardPost.Status.DELETED)
        user = self.request.user
        # 비로그인 또는 family-non-staff: external_visible + published 만
        is_authed = bool(user and user.is_authenticated)
        viewer_role = _resolve_role(user, tenant) if is_authed else ""
        if not _is_staff_role(viewer_role):
            qs = qs.filter(external_visible=True, status=PublicBoardPost.Status.PUBLISHED)
            # hidden 상태는 작성자 본인만 노출
            if is_authed:
                from django.db.models import Q
                qs = PublicBoardPost.objects.filter(tenant=tenant).filter(
                    Q(external_visible=True, status=PublicBoardPost.Status.PUBLISHED) |
                    Q(author=user)
                ).exclude(status=PublicBoardPost.Status.DELETED)
        # 카테고리 필터
        category = (self.request.query_params.get("category") or "").strip()
        if category:
            qs = qs.filter(category=category)
        # 정렬
        ordering = (self.request.query_params.get("ordering") or "latest").strip()
        if ordering == "likes":
            qs = qs.order_by("-is_pinned", "-like_count", "-created_at")
        elif ordering == "replies":
            qs = qs.order_by("-is_pinned", "-reply_count", "-created_at")
        else:
            qs = qs.order_by("-is_pinned", "-created_at")
        # 검색
        q = (self.request.query_params.get("q") or "").strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PublicBoardPostDetailSerializer
        if self.action in ("create", "update", "partial_update"):
            return PublicBoardPostWriteSerializer
        return PublicBoardPostListSerializer

    # ─── CRUD ───

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = self.get_serializer(page, many=True)
            return self.get_paginated_response(ser.data)
        ser = self.get_serializer(qs, many=True)
        return Response(ser.data)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        # 비공개/숨김 가드 (staff/작성자 우회는 get_queryset에서 처리됨)
        viewer_role = _resolve_role(request.user, getattr(request, "tenant", None)) if request.user.is_authenticated else ""
        ctx = {"request": request, "viewer_role": viewer_role}
        # view count 증가 (작성자 본인 제외)
        if request.user.is_authenticated and obj.author_id == request.user.id:
            pass
        else:
            PublicBoardPost.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
        ser = PublicBoardPostDetailSerializer(obj, context=ctx)
        return Response(ser.data)

    def create(self, request, *args, **kwargs):
        ser = PublicBoardPostWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tenant = request.tenant
        user = request.user
        # 블랙리스트 차단 (Phase 4-B)
        if PublicUserBlock.objects.filter(tenant=tenant, blocked_user=user).exists():
            return Response(
                {"detail": "작성이 제한된 사용자입니다. 학원장에게 문의해주세요."},
                status=status.HTTP_403_FORBIDDEN,
            )
        role = _resolve_role(user, tenant)
        display = _resolve_display_name(user)
        # meta.matchup_report_ids whitelist — 학원장 published landing 의 hit_reports 게이트 통과 ID만 허용.
        # 학생/학부모가 raw POST 로 임의 ID 박는 케이스 차단.
        data = dict(ser.validated_data)
        meta = dict(data.pop("meta", {}) or {})
        if "matchup_report_ids" in meta:
            allowed = filter_allowed_report_ids(tenant, meta.get("matchup_report_ids") or [])
            if allowed:
                meta["matchup_report_ids"] = allowed
            else:
                meta.pop("matchup_report_ids", None)
        obj = PublicBoardPost.objects.create(
            tenant=tenant,
            author=user,
            author_display_name=display,
            author_role=role,
            meta=meta,
            **data,
        )
        return Response(
            PublicBoardPostDetailSerializer(obj, context={"request": request, "viewer_role": role}).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()
        if not self._can_edit(request, obj):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        ser = PublicBoardPostWriteSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        # update 시에도 meta.matchup_report_ids 게이트 재검증 (학원장 published list 변경 가능성 대비)
        if "meta" in ser.validated_data:
            new_meta = dict(ser.validated_data.get("meta") or {})
            if "matchup_report_ids" in new_meta:
                allowed = filter_allowed_report_ids(request.tenant, new_meta.get("matchup_report_ids") or [])
                if allowed:
                    new_meta["matchup_report_ids"] = allowed
                else:
                    new_meta.pop("matchup_report_ids", None)
                ser.validated_data["meta"] = new_meta
        ser.save()
        return Response(PublicBoardPostDetailSerializer(obj, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if not self._can_edit(request, obj):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj.status = PublicBoardPost.Status.DELETED
        obj.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _can_edit(self, request, obj) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if obj.author_id == user.id:
            return True
        role = _resolve_role(user, getattr(request, "tenant", None))
        return _is_staff_role(role)

    # ─── Like toggle ───

    @action(detail=True, methods=["post"], url_path="like")
    def like_toggle(self, request, pk=None):
        obj = self.get_object()
        user = request.user
        existing = PublicPostLike.objects.filter(
            user=user, target_kind=PublicPostLike.TargetKind.BOARD, target_id=obj.pk,
        ).first()
        if existing:
            existing.delete()
            obj.refresh_from_db(fields=["like_count"])
            return Response({"liked": False, "like_count": obj.like_count})
        PublicPostLike.objects.create(
            tenant=request.tenant,
            user=user,
            target_kind=PublicPostLike.TargetKind.BOARD,
            target_id=obj.pk,
        )
        obj.refresh_from_db(fields=["like_count"])
        return Response({"liked": True, "like_count": obj.like_count})

    # ─── Moderate (staff) ───

    @action(detail=True, methods=["post"], url_path="moderate")
    def moderate(self, request, pk=None):
        """staff 전용 — pin/hot/hide/external_visible toggle.
        body: { is_pinned?, is_hot?, status?(published/hidden), external_visible? }
        """
        obj = self.get_object()
        updates = {}
        for field in ("is_pinned", "is_hot", "external_visible"):
            if field in request.data:
                updates[field] = bool(request.data[field])
        if "status" in request.data:
            v = request.data["status"]
            if v in (PublicBoardPost.Status.PUBLISHED, PublicBoardPost.Status.HIDDEN, PublicBoardPost.Status.DELETED):
                updates["status"] = v
        if not updates:
            return Response({"detail": "변경할 필드가 없습니다."}, status=status.HTTP_400_BAD_REQUEST)
        for k, v in updates.items():
            setattr(obj, k, v)
        obj.moderated_by = request.user
        obj.moderated_at = timezone.now()
        obj.save(update_fields=list(updates.keys()) + ["moderated_by", "moderated_at", "updated_at"])
        return Response(PublicBoardPostDetailSerializer(obj, context={"request": request}).data)
