
from django.db import models, transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet
from rest_framework.exceptions import PermissionDenied, ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core.permissions import TenantResolvedAndStaff
from apps.api.common.query_params import parse_query_int

from ..models import AccessMode, InactiveVideoEntitlement
from ..serializers import (
    InactiveVideoEntitlementGrantSerializer,
    InactiveVideoEntitlementErrorSerializer,
    InactiveVideoEntitlementMutationSerializer,
    InactiveVideoEntitlementRevokeSerializer,
    InactiveVideoEntitlementSerializer,
    VideoAccessSerializer,
)
from ..services.inactive_entitlements import (
    InactiveVideoEntitlementError,
    grant_inactive_video_entitlement,
    revoke_inactive_video_entitlement,
)
from academy.adapters.db.django import repositories_video as video_repo


class VideoPermissionViewSet(ModelViewSet):
    """Video access overrides (API: video-permissions for backward compat)."""
    serializer_class = VideoAccessSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return video_repo.video_access_all().none()
        return video_repo.video_access_all().filter(
            video__tenant=tenant,
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_set(self, request):
        video_id = request.data.get("video_id")

        # Tenant validation: ensure the video belongs to request.tenant
        from apps.domains.video.models import Video
        if not Video.objects.filter(
            id=video_id,
            tenant=request.tenant,
        ).exists():
            return Response({"error": "Video not found"}, status=404)

        enrollments = request.data.get("enrollments", [])

        if len(enrollments) > 200:
            return Response(
                {"detail": "최대 200건까지 일괄 처리할 수 있습니다."},
                status=400,
            )

        rule = request.data.get("rule")
        access_mode_str = request.data.get("access_mode")
        mode_to_rule = {
            AccessMode.FREE_REVIEW: "free",
            AccessMode.PROCTORED_CLASS: "once",
            AccessMode.BLOCKED: "blocked",
        }

        if rule and not access_mode_str:
            rule_to_mode = {
                "free": AccessMode.FREE_REVIEW,
                "once": AccessMode.PROCTORED_CLASS,
                "blocked": AccessMode.BLOCKED,
            }
            access_mode = rule_to_mode.get(rule, AccessMode.FREE_REVIEW)
            rule = mode_to_rule[access_mode]
        elif access_mode_str:
            try:
                access_mode = AccessMode(access_mode_str)
            except ValueError:
                return Response({"detail": "유효하지 않은 접근 모드입니다."}, status=400)
            rule = mode_to_rule[access_mode]
        else:
            access_mode = AccessMode.PROCTORED_CLASS
            rule = mode_to_rule[access_mode]

        objs = []
        for enrollment_id in enrollments:
            obj, _ = video_repo.video_access_update_or_create_by_ids(
                video_id,
                enrollment_id,
                defaults={
                    "access_mode": access_mode,
                    "rule": rule,
                    "is_override": True,
                },
            )
            objs.append(obj)

        video_repo.video_update(video_id, policy_version=models.F("policy_version") + 1)
        return Response(VideoAccessSerializer(objs, many=True).data)


def _entitlement_error_response(exc: InactiveVideoEntitlementError) -> Response:
    if exc.code in {"student_not_found", "entitlement_not_found"}:
        response_status = status.HTTP_404_NOT_FOUND
    elif exc.code == "actor_forbidden":
        response_status = status.HTTP_403_FORBIDDEN
    else:
        response_status = status.HTTP_400_BAD_REQUEST
    return Response(
        {"code": exc.code, "detail": exc.detail},
        status=response_status,
    )


class InactiveVideoEntitlementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    """Exact staff-authorized video access staged for inactive enrollment use."""

    serializer_class = InactiveVideoEntitlementSerializer
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
            return InactiveVideoEntitlement.objects.none()
        queryset = (
            InactiveVideoEntitlement.objects
            .filter(tenant=tenant)
            .select_related(
                "student__user",
                "enrollment",
                "video__session__lecture",
            )
            .order_by("-granted_at", "-id")
        )
        for parameter, field in (
            ("student_id", "student_id"),
            ("enrollment_id", "enrollment_id"),
            ("video_id", "video_id"),
        ):
            value = parse_query_int(
                self.request.query_params,
                parameter,
                min_value=1,
            )
            if value is None:
                continue
            queryset = queryset.filter(**{field: value})
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by one exact student id.",
            ),
            OpenApiParameter(
                name="enrollment_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by one exact enrollment id.",
            ),
            OpenApiParameter(
                name="video_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by one exact video id.",
            ),
        ],
        responses={
            200: InactiveVideoEntitlementSerializer(many=True),
            400: InactiveVideoEntitlementErrorSerializer,
            403: InactiveVideoEntitlementErrorSerializer,
            404: InactiveVideoEntitlementErrorSerializer,
        },
    )
    def list(self, request, *args, **kwargs):
        try:
            return super().list(request, *args, **kwargs)
        except ValidationError:
            return Response(
                {
                    "code": "invalid_query_parameter",
                    "detail": "entitlement filters must be positive integer ids",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        responses={
            200: InactiveVideoEntitlementSerializer,
            400: InactiveVideoEntitlementErrorSerializer,
            403: InactiveVideoEntitlementErrorSerializer,
            404: InactiveVideoEntitlementErrorSerializer,
        },
    )
    def retrieve(self, request, *args, **kwargs):
        try:
            entitlement_id = int(kwargs.get("pk"))
        except (TypeError, ValueError):
            entitlement_id = None
        entitlement = (
            self.get_queryset().filter(id=entitlement_id).first()
            if entitlement_id is not None
            else None
        )
        if entitlement is None:
            return Response(
                {"code": "entitlement_not_found", "detail": "entitlement not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(InactiveVideoEntitlementSerializer(entitlement).data)

    @extend_schema(
        request=InactiveVideoEntitlementGrantSerializer,
        description=(
            "Grant exact inactive-enrollment access to Academy-hosted revocable media. "
            "YouTube sources fail closed with code video_source_unsupported."
        ),
        responses={
            200: InactiveVideoEntitlementMutationSerializer,
            201: InactiveVideoEntitlementMutationSerializer,
            400: InactiveVideoEntitlementErrorSerializer,
            403: InactiveVideoEntitlementErrorSerializer,
            404: InactiveVideoEntitlementErrorSerializer,
        },
    )
    def create(self, request):
        serializer = InactiveVideoEntitlementGrantSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "code": "invalid_request",
                    "detail": "invalid inactive video entitlement request",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        try:
            result = grant_inactive_video_entitlement(
                tenant=request.tenant,
                student_id=data["student_id"],
                enrollment_id=data["enrollment_id"],
                video_id=data["video_id"],
                access_mode=data["access_mode"],
                source=data["source"],
                source_reference=data["source_reference"],
                reason=data["reason"],
                actor=request.user,
                actor_reference=f"user:{request.user.id}",
                expires_at=data.get("expires_at"),
            )
        except InactiveVideoEntitlementError as exc:
            return _entitlement_error_response(exc)
        payload = {
            "entitlement": InactiveVideoEntitlementSerializer(result.entitlement).data,
            "created": result.created,
            "changed": result.changed,
        }
        return Response(
            payload,
            status=(status.HTTP_201_CREATED if result.created else status.HTTP_200_OK),
        )

    @extend_schema(
        request=InactiveVideoEntitlementRevokeSerializer,
        responses={
            200: InactiveVideoEntitlementMutationSerializer,
            400: InactiveVideoEntitlementErrorSerializer,
            403: InactiveVideoEntitlementErrorSerializer,
            404: InactiveVideoEntitlementErrorSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        serializer = InactiveVideoEntitlementRevokeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "detail": "valid revoke reason is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = revoke_inactive_video_entitlement(
                tenant=request.tenant,
                entitlement_id=int(pk),
                reason=serializer.validated_data["reason"],
                actor=request.user,
                actor_reference=f"user:{request.user.id}",
            )
        except (TypeError, ValueError):
            return Response(
                {"code": "entitlement_not_found", "detail": "entitlement not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except InactiveVideoEntitlementError as exc:
            return _entitlement_error_response(exc)
        return Response(
            {
                "entitlement": InactiveVideoEntitlementSerializer(result.entitlement).data,
                "created": result.created,
                "changed": result.changed,
            }
        )
