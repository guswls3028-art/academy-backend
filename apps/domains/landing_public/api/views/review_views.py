"""수강후기 ViewSet — 외부 공개 list/detail + 학원 family 작성 + staff 승인."""
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndMember, TenantResolvedAndStaff

from ..serializers import (
    PublicReviewDetailSerializer,
    PublicReviewListSerializer,
    PublicReviewModerateSerializer,
    PublicReviewWriteSerializer,
    _is_staff_role,
    _resolve_display_name,
    _resolve_role,
)
from ...models import PublicPostLike, PublicReview, PublicUserBlock
from ..pagination import LandingPublicPagination


class PublicReviewViewSet(viewsets.GenericViewSet):
    """공개 수강후기.

    list / retrieve: 비로그인 OK (approved 만)
    create: 학생/학부모 로그인 필요 → status=pending 생성
    moderate (approve/reject/pin/verify): staff only
    like (toggle): family only
    """

    queryset = PublicReview.objects.all()
    pagination_class = LandingPublicPagination

    def get_permissions(self):
        if self.action in ("list", "retrieve", "summary"):
            return [TenantResolved()]
        if self.action == "moderate":
            return [TenantResolvedAndStaff()]
        return [TenantResolvedAndMember()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicReview.objects.none()
        qs = PublicReview.objects.filter(tenant=tenant)
        user = self.request.user
        is_authed = bool(user and user.is_authenticated)
        viewer_role = _resolve_role(user, tenant) if is_authed else ""
        if not _is_staff_role(viewer_role):
            from django.db.models import Q
            if is_authed:
                qs = qs.filter(Q(status=PublicReview.Status.APPROVED) | Q(author=user))
            else:
                qs = qs.filter(status=PublicReview.Status.APPROVED)
        # 정렬
        ordering = (self.request.query_params.get("ordering") or "latest").strip()
        if ordering == "rating":
            qs = qs.order_by("-is_pinned", "-rating", "-created_at")
        elif ordering == "likes":
            qs = qs.order_by("-is_pinned", "-like_count", "-created_at")
        else:
            qs = qs.order_by("-is_pinned", "-created_at")
        # 학년/과목 필터
        grade = (self.request.query_params.get("grade") or "").strip()
        if grade:
            qs = qs.filter(grade=grade)
        subject = (self.request.query_params.get("subject") or "").strip()
        if subject:
            qs = qs.filter(subject=subject)
        min_rating = self.request.query_params.get("min_rating")
        if min_rating:
            try:
                qs = qs.filter(rating__gte=int(min_rating))
            except (TypeError, ValueError):
                pass
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PublicReviewDetailSerializer
        if self.action in ("create", "update", "partial_update"):
            return PublicReviewWriteSerializer
        return PublicReviewListSerializer

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
        return Response(PublicReviewDetailSerializer(obj, context={"request": request}).data)

    def create(self, request, *args, **kwargs):
        ser = PublicReviewWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tenant = request.tenant
        user = request.user
        # 블랙리스트 차단 (Phase 4-B)
        if PublicUserBlock.objects.filter(tenant=tenant, blocked_user=user).exists():
            return Response(
                {"detail": "후기 작성이 제한된 사용자입니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        role = _resolve_role(user, tenant)
        # 학생/학부모만 작성 가능 — staff는 모더레이션 권한 분리 (자기 학원 후기 자작 차단)
        if _is_staff_role(role):
            return Response(
                {"detail": "학원 운영자는 수강 후기를 작성할 수 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        display = _resolve_display_name(user)
        obj = PublicReview.objects.create(
            tenant=tenant,
            author=user,
            author_display_name=display,
            author_role=role,
            status=PublicReview.Status.PENDING,
            **ser.validated_data,
        )
        return Response(
            PublicReviewDetailSerializer(obj, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if obj.author_id != user.id and not _is_staff_role(_resolve_role(user, request.tenant)):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        ser = PublicReviewWriteSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(PublicReviewDetailSerializer(obj, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if obj.author_id != user.id and not _is_staff_role(_resolve_role(user, request.tenant)):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj.status = PublicReview.Status.HIDDEN
        obj.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="like")
    def like_toggle(self, request, pk=None):
        obj = self.get_object()
        user = request.user
        # tenant 필터 — cross-tenant 누수 차단(core.md §1)
        existing = PublicPostLike.objects.filter(
            tenant=request.tenant,
            user=user, target_kind=PublicPostLike.TargetKind.REVIEW, target_id=obj.pk,
        ).first()
        if existing:
            existing.delete()
            obj.refresh_from_db(fields=["like_count"])
            return Response({"liked": False, "like_count": obj.like_count})
        PublicPostLike.objects.create(
            tenant=request.tenant,
            user=user,
            target_kind=PublicPostLike.TargetKind.REVIEW,
            target_id=obj.pk,
        )
        obj.refresh_from_db(fields=["like_count"])
        return Response({"liked": True, "like_count": obj.like_count})

    @action(detail=True, methods=["post"], url_path="moderate")
    def moderate(self, request, pk=None):
        """staff 전용 — status/is_pinned/is_verified 갱신."""
        obj = self.get_object()
        ser = PublicReviewModerateSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        obj.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
        return Response(PublicReviewDetailSerializer(obj, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """공개 KPI — 총 후기 수 / 평균 평점 / 별점 분포 (approved 만)."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"count": 0, "average": 0.0, "distribution": {}})
        qs = PublicReview.objects.filter(tenant=tenant, status=PublicReview.Status.APPROVED)
        total = qs.count()
        if total == 0:
            return Response({"count": 0, "average": 0.0, "distribution": {str(i): 0 for i in range(1, 6)}})
        from django.db.models import Avg, Count
        avg = qs.aggregate(a=Avg("rating"))["a"] or 0.0
        dist_rows = qs.values("rating").annotate(c=Count("id"))
        dist = {str(i): 0 for i in range(1, 6)}
        for row in dist_rows:
            dist[str(row["rating"])] = row["c"]
        return Response({"count": total, "average": round(float(avg), 2), "distribution": dist})
