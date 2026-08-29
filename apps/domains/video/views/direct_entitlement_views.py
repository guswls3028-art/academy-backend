from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.video.direct_entitlement_serializers import (
    DirectVideoEntitlementErrorSerializer,
    DirectVideoEntitlementGrantSerializer,
    DirectVideoEntitlementMutationSerializer,
    DirectVideoEntitlementRevokeSerializer,
    DirectVideoEntitlementSerializer,
)
from apps.domains.video.models import DirectVideoEntitlement, Video
from apps.domains.video.services.direct_entitlements import (
    DirectVideoEntitlementError,
    grant_direct_video_entitlement,
    revoke_direct_video_entitlement,
)


def _error_response(exc: DirectVideoEntitlementError) -> Response:
    if exc.code in {"student_not_found", "video_not_found", "entitlement_not_found"}:
        response_status = status.HTTP_404_NOT_FOUND
    elif exc.code == "actor_forbidden":
        response_status = status.HTTP_403_FORBIDDEN
    else:
        response_status = status.HTTP_400_BAD_REQUEST
    return Response(
        {"code": exc.code, "detail": exc.detail},
        status=response_status,
    )


class DirectVideoEntitlementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    """Exact staff-authorized access to one video without enrollment."""

    serializer_class = DirectVideoEntitlementSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def permission_denied(self, request, message=None, code=None):
        raise PermissionDenied(
            {
                "code": "staff_video_permission_required",
                "detail": message or "staff video permission is required",
            },
            code=code,
        )

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return DirectVideoEntitlement.objects.none()
        return (
            DirectVideoEntitlement.objects
            .filter(tenant=tenant)
            .select_related(
                "tenant",
                "student__user",
                "video__session__lecture",
            )
            .order_by("-granted_at", "-id")
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="video_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Return history for one exact same-tenant video.",
            ),
        ],
        responses={
            200: DirectVideoEntitlementSerializer(many=True),
            400: DirectVideoEntitlementErrorSerializer,
            403: DirectVideoEntitlementErrorSerializer,
            404: DirectVideoEntitlementErrorSerializer,
        },
    )
    def list(self, request, *args, **kwargs):
        try:
            video_id = parse_query_int(
                request.query_params,
                "video_id",
                min_value=1,
            )
        except ValidationError:
            video_id = None
        if video_id is None:
            return Response(
                {
                    "code": "video_id_required",
                    "detail": "one positive video_id is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not Video.objects.filter(id=video_id, tenant=request.tenant).exists():
            return Response(
                {"code": "video_not_found", "detail": "exact video not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        queryset = self.filter_queryset(self.get_queryset().filter(video_id=video_id))
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                DirectVideoEntitlementSerializer(page, many=True).data
            )
        return Response(
            {"results": DirectVideoEntitlementSerializer(queryset, many=True).data}
        )

    @extend_schema(
        request=DirectVideoEntitlementGrantSerializer,
        responses={
            200: DirectVideoEntitlementMutationSerializer,
            201: DirectVideoEntitlementMutationSerializer,
            400: DirectVideoEntitlementErrorSerializer,
            403: DirectVideoEntitlementErrorSerializer,
            404: DirectVideoEntitlementErrorSerializer,
        },
    )
    def create(self, request):
        serializer = DirectVideoEntitlementGrantSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "code": "invalid_request",
                    "detail": "invalid direct video entitlement request",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        try:
            result = grant_direct_video_entitlement(
                tenant=request.tenant,
                student_id=data["student_id"],
                video_id=data["video_id"],
                reason=data["reason"],
                actor=request.user,
                actor_reference=f"user:{request.user.id}",
                source_reference="admin-video-permission",
                confirmed_regrant=data["confirmed_regrant"],
            )
        except DirectVideoEntitlementError as exc:
            return _error_response(exc)
        payload = {
            "entitlement": DirectVideoEntitlementSerializer(result.entitlement).data,
            "created": result.created,
            "changed": result.changed,
        }
        return Response(
            payload,
            status=(status.HTTP_201_CREATED if result.created else status.HTTP_200_OK),
        )

    @extend_schema(
        request=DirectVideoEntitlementRevokeSerializer,
        responses={
            200: DirectVideoEntitlementMutationSerializer,
            400: DirectVideoEntitlementErrorSerializer,
            403: DirectVideoEntitlementErrorSerializer,
            404: DirectVideoEntitlementErrorSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        serializer = DirectVideoEntitlementRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "detail": "valid revoke reason is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            entitlement_id = int(pk)
        except (TypeError, ValueError):
            return Response(
                {"code": "entitlement_not_found", "detail": "entitlement not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            result = revoke_direct_video_entitlement(
                tenant=request.tenant,
                entitlement_id=entitlement_id,
                reason=serializer.validated_data["reason"],
                actor=request.user,
                actor_reference=f"user:{request.user.id}",
            )
        except DirectVideoEntitlementError as exc:
            return _error_response(exc)
        return Response(
            {
                "entitlement": DirectVideoEntitlementSerializer(result.entitlement).data,
                "created": result.created,
                "changed": result.changed,
            }
        )
