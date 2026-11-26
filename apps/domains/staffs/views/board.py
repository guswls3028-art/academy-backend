from collections.abc import Mapping
from functools import cached_property

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.infrastructure.storage.r2 import (
    get_object_bytes_r2_storage, put_small_storage_object,
)
from apps.support.community.storage_cleanup_dependencies import schedule_detached_storage_cleanup
from apps.support.inventory.storage_cleanup_dependencies import lock_reserved_upload, reserve_staff_board_upload
from apps.core.permissions import TenantResolvedAndStaff
from apps.core.services.tenant_access import get_active_membership_role
from apps.domains.staffs.models import StaffBoardPost, StaffBoardReply
from apps.domains.staffs.services.board_writes import BoardWrite


FILE_LIMIT = 10 * 1024 * 1024
REQUEST_KEY = OpenApiParameter("Idempotency-Key", OpenApiTypes.UUID, OpenApiParameter.HEADER)
REPLY_ID = OpenApiParameter("reply_id", OpenApiTypes.INT, OpenApiParameter.PATH)


class BoardStorageUnavailable(APIException):
    status_code = 503
    default_detail = "파일 저장소에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."


class StaffBoardErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


class StaffBoardReplySerializer(serializers.ModelSerializer):
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = StaffBoardReply
        fields = ("id", "body", "author_name", "created_at", "can_edit", "can_delete")
        read_only_fields = ("id", "author_name", "created_at")

    def get_can_edit(self, reply) -> bool:
        return reply.author_id == self.context["request"].user.pk

    def get_can_delete(self, reply) -> bool:
        return self.context["view"].can_delete_content(reply)


class StaffBoardPostSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False, max_length=255)
    reply_count = serializers.IntegerField(read_only=True, default=0)
    can_edit = serializers.SerializerMethodField()
    can_remove_attachment = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = StaffBoardPost
        fields = ("id", "category", "title", "body", "author_name", "created_at",
                  "attachment_name", "file", "reply_count", "can_edit", "can_remove_attachment", "can_delete")
        read_only_fields = ("id", "author_name", "created_at", "attachment_name")

    def get_can_edit(self, post) -> bool:
        return post.author_id == self.context["request"].user.pk

    def get_can_remove_attachment(self, post) -> bool:
        return bool(post.attachment_key) and self.get_can_delete(post)

    def get_can_delete(self, post) -> bool:
        return self.context["view"].can_delete_content(post)

    def validate(self, attrs):
        file = attrs.get("file")
        if file is not None:
            if attrs.get("category") != StaffBoardPost.Category.RESOURCE:
                raise serializers.ValidationError({"file": "자료실에만 파일을 첨부할 수 있습니다."})
            if file.size > FILE_LIMIT:
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


class BoardSearchSerializer(serializers.Serializer):
    q = serializers.CharField(
        default="", allow_blank=True, max_length=100,
        error_messages={"max_length": "검색어는 100자 이하로 입력해 주세요."},
    )


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

    @cached_property
    def manages_board(self):
        return get_active_membership_role(self.request.user, self.request.tenant) in ("owner", "admin")

    def can_delete_content(self, item):
        return item.author_id == self.request.user.pk or self.manages_board

    def locked_post(self, pk, *, include_deleted=False):
        posts = StaffBoardPost.objects.select_for_update().filter(tenant=self.request.tenant)
        if not include_deleted:
            posts = posts.filter(deleted_at__isnull=True)
        return get_object_or_404(posts, pk=pk)

    def get_queryset(self):
        return StaffBoardPost.objects.filter(tenant=self.request.tenant, deleted_at__isnull=True).annotate(
            reply_count=Count("replies")
        ).order_by("-id")

    @extend_schema(
        parameters=[
            OpenApiParameter("category", str, enum=StaffBoardPost.Category.values),
            OpenApiParameter("q", {"type": "string", "maxLength": 100},
                             description="제목·본문·첨부 파일명에 포함된 검색어. 앞뒤 공백을 제외하고 최대 100자."),
        ],
        responses=StaffBoardSummarySerializer(many=True),
    )
    def list(self, request):
        posts = self.get_queryset().defer("body")
        category = request.query_params.get("category")
        if category is not None:
            if category not in StaffBoardPost.Category.values:
                raise serializers.ValidationError({"category": "게시판 종류를 확인해 주세요."})
            posts = posts.filter(category=category)
        search = BoardSearchSerializer(data=request.query_params)
        search.is_valid(raise_exception=True)
        keyword = search.validated_data["q"]
        if keyword:
            posts = posts.filter(
                Q(title__icontains=keyword) | Q(body__icontains=keyword) | Q(attachment_name__icontains=keyword)
            )
        page = self.paginate_queryset(posts)
        serializer = StaffBoardSummarySerializer(page, many=True, context=self.get_serializer_context())
        return self.get_paginated_response(serializer.data)

    @extend_schema(responses=StaffBoardDetailSerializer)
    def retrieve(self, request, pk=None):
        serializer = StaffBoardDetailSerializer(self.get_object(), context=self.get_serializer_context())
        return Response(serializer.data)

    @extend_schema(parameters=[REQUEST_KEY], responses={200: StaffBoardPostSerializer, 201: StaffBoardPostSerializer, 409: StaffBoardErrorSerializer})
    def create(self, request):
        if len(request.FILES.getlist("file")) > 1:
            raise serializers.ValidationError({"file": "파일 한 개만 첨부할 수 있습니다."})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data.pop("file", None)
        write = BoardWrite(request, "post", serializer.validated_data, upload)
        previous = write.existing_result()
        if previous:
            return Response(self.get_serializer(get_object_or_404(self.get_queryset(), pk=previous)).data)
        intent = reserve_staff_board_upload(tenant_id=request.tenant.pk) if upload else None
        with transaction.atomic():
            receipt = write.reserve()
            previous = write.result(receipt)
            if previous:
                if intent:
                    intent.delete()
                return Response(self.get_serializer(get_object_or_404(self.get_queryset(), pk=previous)).data)
            if intent:
                if lock_reserved_upload(intent) is None:
                    raise BoardStorageUnavailable()
                try:
                    put_small_storage_object(fileobj=upload, key=intent.object_key)
                except Exception as exc:
                    raise BoardStorageUnavailable() from exc
            post = serializer.save(
                tenant=request.tenant, author=request.user, author_name=author_name(request.user),
                attachment_key=intent.object_key if intent else "",
                attachment_name=upload.name if upload else "",
            )
            write.finish(receipt, post)
            if intent:
                intent.delete()
        return Response(serializer.data, status=201)

    @extend_schema(request=StaffBoardEditSerializer, responses=StaffBoardPostSerializer)
    @transaction.atomic
    def partial_update(self, request, pk=None):
        post = self.locked_post(pk)
        if post.author_id != request.user.pk:
            raise PermissionDenied()
        if not isinstance(request.data, Mapping) or not request.data or set(request.data) - {"title", "body"}:
            raise serializers.ValidationError("제목과 본문만 수정할 수 있습니다.")
        serializer = StaffBoardEditSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(post, field, value)
        post.save(update_fields=list(serializer.validated_data))
        return Response(self.get_serializer(self.get_queryset().get(pk=post.pk)).data)

    @extend_schema(parameters=[REQUEST_KEY], request=StaffBoardReplySerializer,
                   responses={200: StaffBoardReplySerializer, 201: StaffBoardReplySerializer, 409: StaffBoardErrorSerializer})
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def replies(self, request, pk=None):
        post = self.locked_post(pk)
        context = self.get_serializer_context()
        reply = StaffBoardReplySerializer(data=request.data, context=context)
        reply.is_valid(raise_exception=True)
        write = BoardWrite(request, f"post/{post.pk}/reply", reply.validated_data)
        receipt = write.reserve()
        previous = write.result(receipt)
        if previous:
            return Response(StaffBoardReplySerializer(get_object_or_404(post.replies, pk=previous), context=context).data)
        write.finish(receipt, reply.save(post=post, author=request.user, author_name=author_name(request.user)))
        return Response(reply.data, status=201)

    @extend_schema(parameters=[REPLY_ID], request=StaffBoardReplySerializer, responses=StaffBoardReplySerializer)
    @action(detail=True, methods=["patch"], url_path=r"replies/(?P<reply_id>[0-9]+)")
    @transaction.atomic
    def edit_reply(self, request, pk=None, reply_id=None):
        reply = get_object_or_404(self.locked_post(pk).replies, pk=reply_id)
        if reply.author_id != request.user.pk:
            raise PermissionDenied()
        if not isinstance(request.data, Mapping) or set(request.data) != {"body"}:
            raise serializers.ValidationError("댓글 내용만 수정할 수 있습니다.")
        serializer = StaffBoardReplySerializer(reply, data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(parameters=[REPLY_ID], request=None, responses={204: None})
    @edit_reply.mapping.delete
    @transaction.atomic
    def delete_reply(self, request, pk=None, reply_id=None):
        reply = get_object_or_404(self.locked_post(pk).replies, pk=reply_id)
        if not self.can_delete_content(reply):
            raise PermissionDenied()
        reply.delete()
        return Response(status=204)

    @extend_schema(responses={204: None})
    @transaction.atomic
    def destroy(self, request, pk=None):
        post = self.locked_post(pk, include_deleted=True)
        if not self.can_delete_content(post):
            raise PermissionDenied()
        if post.deleted_at is None:
            post.deleted_at = timezone.now()
            key = post.attachment_key
            post.attachment_key = ""
            post.attachment_name = ""
            post.save(update_fields=["deleted_at", "attachment_key", "attachment_name"])
            if key:
                schedule_detached_storage_cleanup(tenant_id=request.tenant.pk, object_keys=[key])
        return Response(status=204)

    @extend_schema(
        responses={
            (200, "application/octet-stream"): OpenApiTypes.BINARY,
            (404, "application/json"): StaffBoardErrorSerializer,
            (503, "application/json"): StaffBoardErrorSerializer,
        },
        parameters=[OpenApiParameter("Content-Disposition", str, OpenApiParameter.HEADER, response=[200])],
    )
    @action(detail=True)
    def attachment(self, request, pk=None):
        post = self.get_object()
        if not post.attachment_key:
            raise NotFound("첨부파일을 찾을 수 없습니다.")
        try:
            data = get_object_bytes_r2_storage(key=post.attachment_key, max_bytes=FILE_LIMIT, timeout_seconds=15)
        except Exception as exc:
            raise BoardStorageUnavailable() from exc
        if data is None:
            raise NotFound("첨부파일을 찾을 수 없습니다.")
        return HttpResponse(data, content_type="application/octet-stream", headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": content_disposition_header(True, post.attachment_name),
            "Content-Length": str(len(data)),
            "Access-Control-Expose-Headers": "Content-Disposition",
        })

    @extend_schema(request=None, responses={204: None})
    @attachment.mapping.delete
    def remove_attachment(self, request, pk=None):
        with transaction.atomic():
            post = self.locked_post(pk)
            if not self.can_delete_content(post):
                raise PermissionDenied()
            if post.attachment_key:
                key = post.attachment_key
                post.attachment_key = ""
                post.attachment_name = ""
                post.save(update_fields=["attachment_key", "attachment_name"])
                schedule_detached_storage_cleanup(tenant_id=request.tenant.pk, object_keys=[key])
        return Response(status=204)
