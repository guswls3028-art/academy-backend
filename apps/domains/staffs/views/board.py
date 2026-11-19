from collections.abc import Mapping
from uuid import uuid4

from django.db import transaction
from django.db.models import Count
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage, upload_fileobj_to_r2_storage
from apps.support.inventory.storage_cleanup_dependencies import compensate_unattached_storage_object
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.staffs.models import StaffBoardPost, StaffBoardReply


class BoardStorageUnavailable(APIException):
    status_code = 503
    default_detail = "파일 저장소에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."


class StaffBoardReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffBoardReply
        fields = ("id", "body", "author_name", "created_at")
        read_only_fields = ("id", "author_name", "created_at")


class StaffBoardPostSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False, max_length=255)
    reply_count = serializers.IntegerField(read_only=True, default=0)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = StaffBoardPost
        fields = ("id", "category", "title", "body", "author_name", "created_at",
                  "attachment_name", "file", "reply_count", "can_edit")
        read_only_fields = ("id", "author_name", "created_at", "attachment_name")

    def get_can_edit(self, post) -> bool:
        return post.author_id == self.context["request"].user.pk

    def validate(self, attrs):
        file = attrs.get("file")
        if file is not None:
            if attrs.get("category") != StaffBoardPost.Category.RESOURCE:
                raise serializers.ValidationError({"file": "자료실에만 파일을 첨부할 수 있습니다."})
            if file.size > 10 * 1024 * 1024:
                raise serializers.ValidationError({"file": "10 MiB 이하 파일을 선택해 주세요."})
        return attrs


class StaffBoardSummarySerializer(StaffBoardPostSerializer):
    class Meta(StaffBoardPostSerializer.Meta):
        fields = tuple(name for name in StaffBoardPostSerializer.Meta.fields if name not in ("body", "file"))


class StaffBoardDetailSerializer(StaffBoardPostSerializer):
    replies = StaffBoardReplySerializer(many=True, read_only=True)

    class Meta(StaffBoardPostSerializer.Meta):
        fields = (*StaffBoardPostSerializer.Meta.fields, "replies")


class StaffBoardEditSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffBoardPost
        fields = ("title", "body")


class BoardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 20


def author_name(user):
    return user.name or user.get_full_name() or "교직원"


class StaffBoardViewSet(viewsets.GenericViewSet):
    permission_classes = [TenantResolvedAndStaff]
    queryset = StaffBoardPost.objects.none()
    serializer_class = StaffBoardPostSerializer
    pagination_class = BoardPagination

    def get_queryset(self):
        return StaffBoardPost.objects.filter(tenant=self.request.tenant).annotate(
            reply_count=Count("replies")
        ).order_by("-id")

    @extend_schema(
        parameters=[OpenApiParameter("category", str, enum=StaffBoardPost.Category.values)],
        responses=StaffBoardSummarySerializer(many=True),
    )
    def list(self, request):
        posts = self.get_queryset().defer("body")
        category = request.query_params.get("category")
        if category is not None:
            if category not in StaffBoardPost.Category.values:
                raise serializers.ValidationError({"category": "게시판 종류를 확인해 주세요."})
            posts = posts.filter(category=category)
        page = self.paginate_queryset(posts)
        serializer = StaffBoardSummarySerializer(page, many=True, context=self.get_serializer_context())
        return self.get_paginated_response(serializer.data)

    @extend_schema(responses=StaffBoardDetailSerializer)
    def retrieve(self, request, pk=None):
        serializer = StaffBoardDetailSerializer(self.get_object(), context=self.get_serializer_context())
        return Response(serializer.data)

    def create(self, request):
        if len(request.FILES.getlist("file")) > 1:
            raise serializers.ValidationError({"file": "파일 한 개만 첨부할 수 있습니다."})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data.pop("file", None)
        key = f"tenants/{request.tenant.pk}/staff-board/{uuid4().hex}" if upload else ""
        if upload:
            try:
                upload_fileobj_to_r2_storage(fileobj=upload, key=key, content_type="application/octet-stream")
            except Exception as exc:
                compensate_unattached_storage_object(tenant_id=request.tenant.pk, key=key, uncertain_write=True)
                raise BoardStorageUnavailable() from exc
        try:
            with transaction.atomic():
                serializer.save(
                    tenant=request.tenant,
                    author=request.user,
                    author_name=author_name(request.user),
                    attachment_key=key,
                    attachment_name=upload.name if upload else "",
                )
        except Exception:
            if key:
                compensate_unattached_storage_object(tenant_id=request.tenant.pk, key=key)
            raise
        return Response(serializer.data, status=201)

    @extend_schema(request=StaffBoardEditSerializer, responses=StaffBoardPostSerializer)
    def partial_update(self, request, pk=None):
        post = self.get_object()
        if post.author_id != request.user.pk:
            raise PermissionDenied()
        if not isinstance(request.data, Mapping) or not request.data or set(request.data) - {"title", "body"}:
            raise serializers.ValidationError("제목과 본문만 수정할 수 있습니다.")
        serializer = StaffBoardEditSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(post, field, value)
        post.save(update_fields=list(serializer.validated_data))
        return Response(self.get_serializer(post).data)

    @extend_schema(request=StaffBoardReplySerializer, responses={201: StaffBoardReplySerializer})
    @action(detail=True, methods=["post"])
    def replies(self, request, pk=None):
        post = self.get_object()
        reply = StaffBoardReplySerializer(data=request.data)
        reply.is_valid(raise_exception=True)
        reply.save(post=post, author_name=author_name(request.user))
        return Response(reply.data, status=201)

    @extend_schema(responses=inline_serializer(
        name="StaffBoardDownload", fields={"url": serializers.URLField()},
    ))
    @action(detail=True)
    def download(self, request, pk=None):
        post = self.get_object()
        if not post.attachment_key:
            raise NotFound()
        try:
            url = generate_presigned_get_url_storage(
                key=post.attachment_key, filename=post.attachment_name,
                content_type="application/octet-stream", expires_in=120,
            )
        except Exception as exc:
            raise BoardStorageUnavailable() from exc
        return Response({"url": url}, headers={"Cache-Control": "private, no-store"})
