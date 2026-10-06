from django.http import Http404
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field, inline_serializer

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import TenantMembership
from apps.core.permissions import TenantResolved
from apps.core.services.tenant_access import get_authorized_tenant_role
from apps.domains.landing_public.models.resource import (
    PublicResourceBoardAccess,
    PublicResourceFile,
    PublicResourcePost,
)
from apps.domains.landing_public.services.resource_files import resource_content_disposition, validate_resource_file
from apps.infrastructure.storage.r2 import (
    delete_object_r2_admin,
    generate_presigned_get_url_admin,
    upload_fileobj_to_r2_admin,
)

from apps.domains.landing_public.services.resource_reader import (
    delete_reader_objects, prepare_reader, reader_payload, reader_state,
)

PUBLISHER_ROLES = ("owner", "admin", "teacher")
LINK_TTL = 300


def can_publish(request):
    user, tenant = request.user, request.tenant
    if not user or not user.is_authenticated or not user.is_active:
        return False
    access = PublicResourceBoardAccess.objects.filter(tenant=tenant).first()
    if not access or not access.publisher_one_id or not access.publisher_two_id:
        return False
    return (
        user.id in (access.publisher_one_id, access.publisher_two_id)
        and get_authorized_tenant_role(user, tenant) in PUBLISHER_ROLES
    )


def require_publisher(request, *, lock=False):
    if lock:
        # Keep the core lifecycle's User → membership lock order for revocation.
        locked_user = get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
        if not locked_user or not locked_user.is_active:
            raise PermissionDenied("자료 게시 권한이 없습니다.")
        request.user.is_active = locked_user.is_active
        TenantMembership.objects.select_for_update().filter(tenant=request.tenant, user=request.user).first()
        PublicResourceBoardAccess.objects.select_for_update().filter(tenant=request.tenant).first()
    if not can_publish(request):
        raise PermissionDenied("자료 게시 권한이 없습니다.")


class ResourceFileSerializer(serializers.ModelSerializer):
    extension = serializers.SerializerMethodField()
    reader_status = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_reader_status(self, obj):
        return reader_state(obj)

    @extend_schema_field(serializers.CharField())
    def get_extension(self, obj):
        # The original name is authoritative; the legacy column only fits known document types.
        return obj.filename.rsplit(".", 1)[-1].lower()[:200] if "." in obj.filename else ""

    class Meta:
        model = PublicResourceFile
        fields = ("id", "filename", "extension", "size", "reader_status")


class ResourcePostSerializer(serializers.ModelSerializer):
    files = serializers.SerializerMethodField()

    @extend_schema_field(ResourceFileSerializer(many=True))
    def get_files(self, obj):
        return ResourceFileSerializer(
            sorted([file for file in obj.files.all() if not file.is_removed and file.is_ready],
                   key=lambda file: (file.position, file.created_at, str(file.pk))), many=True
        ).data

    class Meta:
        model = PublicResourcePost
        fields = ("id", "category", "title", "content", "author_display_name", "created_at", "updated_at", "files")


class ResourceEditConflict(APIException):
    status_code = 409
    default_detail = "다른 게시자가 이 자료를 수정했습니다. 입력한 내용은 유지됩니다. 최신 게시물을 확인해주세요."
    default_code = "resource_edit_conflict"


class ResourcePublishConflict(APIException):
    status_code = 409
    default_code = "resource_already_published"


class ResourceWriteSerializer(serializers.Serializer):
    request_id = serializers.UUIDField(required=False)
    expected_updated_at = serializers.DateTimeField(required=False)
    title = serializers.CharField(max_length=200, trim_whitespace=True)
    category = serializers.ChoiceField(choices=("matchup", "analysis"))
    content = serializers.CharField(max_length=20000, allow_blank=True, default="")
    file_ids = serializers.ListField(child=serializers.UUIDField(), min_length=0, max_length=5)

    def validate_file_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("같은 파일을 중복 첨부할 수 없습니다.")
        return value


class ResourcePagination(PageNumberPagination):
    page_size = 20


class PublicResourcePostViewSet(viewsets.GenericViewSet):
    pagination_class = ResourcePagination
    permission_classes = [TenantResolved]
    serializer_class = ResourcePostSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PublicResourcePost.objects.none()
        return PublicResourcePost.objects.filter(tenant=self.request.tenant, status="published").prefetch_related(
            Prefetch("files", queryset=PublicResourceFile.objects.defer("reader_data", "reader_object_keys"))
        )

    @extend_schema(auth=[], parameters=[OpenApiParameter("category", enum=["matchup", "analysis"])])
    def list(self, request):
        queryset = self.get_queryset()
        category = request.query_params.get("category")
        if category:
            if category not in ("matchup", "analysis"):
                raise ValidationError({"category": "게시판 분류를 확인해주세요."})
            queryset = queryset.filter(category=category)
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(queryset[:100], many=True).data)

    @extend_schema(auth=[])
    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(responses=inline_serializer("ResourceCapability", fields={"can_publish": serializers.BooleanField()}))
    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        return Response({"can_publish": can_publish(request)}, headers={"Cache-Control": "no-store"})

    def _save(self, request, pk=None):
        require_publisher(request)
        serializer = ResourceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        file_ids = data["file_ids"]
        request_id = data.get("request_id") or uuid.uuid4()
        with transaction.atomic():
            require_publisher(request, lock=True)
            post = get_object_or_404(self.get_queryset().select_for_update(), pk=pk) if pk else None
            files = list(
                PublicResourceFile.objects.select_for_update()
                .filter(tenant=request.tenant, pk__in=file_ids)
                .order_by("id")
            )
            if len(files) != len(file_ids):
                raise ValidationError({"file_ids": "첨부 파일을 찾을 수 없습니다. 다시 올려주세요."})
            if not pk:
                existing = PublicResourcePost.objects.filter(tenant=request.tenant, request_id=request_id).first()
                if existing:
                    current_ids = list(existing.files.filter(is_removed=False).order_by("position", "created_at", "id").values_list("id", flat=True))
                    if (
                        existing.author_id == request.user.pk
                        and existing.status == "published"
                        and existing.title == data["title"]
                        and existing.content == data["content"]
                        and existing.category == data["category"]
                        and current_ids == file_ids
                    ):
                        return existing, False
                    if existing.author_id == request.user.pk and existing.status == "published":
                        raise ResourcePublishConflict({
                            "detail": "앞선 요청으로 이미 게시된 자료입니다. 입력한 변경 내용은 유지됩니다. 수정 내용 게시를 눌러 반영해주세요.",
                            "post_id": existing.pk,
                            "updated_at": existing.updated_at.isoformat(),
                            "file_ids": [str(value) for value in existing.files.values_list("id", flat=True)],
                            "published_title": existing.title,
                            "published_content": existing.content,
                            "published_filenames": list(existing.files.filter(is_removed=False, is_ready=True).values_list("filename", flat=True)),
                        })
                    raise ValidationError({"request_id": "이미 처리된 게시 요청입니다. 게시판을 확인해주세요."})
            if post is not None and data.get("expected_updated_at") not in (None, post.updated_at):
                current_ids = list(post.files.filter(is_removed=False, is_ready=True).order_by("position", "created_at", "id").values_list("id", flat=True))
                if (post.title == data["title"] and post.content == data["content"]
                        and post.category == data["category"] and current_ids == file_ids):
                    # A successful PATCH may lose its response; replay must remain safe.
                    return post, False
                raise ResourceEditConflict()
            for file in files:
                if not file.is_ready:
                    raise ValidationError(
                        {"file_ids": "업로드가 완료되지 않았거나 정리 중인 첨부입니다. 다시 올려주세요."}
                    )
                if file.post_id is None:
                    if file.uploaded_by_id != request.user.pk:
                        raise ValidationError({"file_ids": "직접 올린 파일만 새로 첨부할 수 있습니다."})
                elif post is None or file.post_id != post.pk:
                    raise ValidationError({"file_ids": "다른 게시물의 파일은 옮길 수 없습니다."})
            if any(reader_state(file) not in ("ready", "unsupported") for file in files):
                raise ValidationError({"file_ids": "문서 본문 준비가 끝난 뒤 게시해주세요. 실패한 문서는 다시 준비하거나 교체할 수 있습니다."})
            if not data["content"].strip() and not any(reader_state(file) == "ready" for file in files):
                raise ValidationError({"content": "방문자가 바로 읽을 본문을 작성하거나 PDF·한글·이미지·텍스트 문서를 올려주세요."})
            if post is None:
                post = PublicResourcePost.objects.create(
                    tenant=request.tenant,
                    author=request.user,
                    author_display_name=request.user.name or request.user.get_full_name() or "게시자",
                    request_id=request_id,
                    title=data["title"],
                    category=data["category"],
                    content=data["content"],
                )
                created = True
            else:
                post.title, post.category, post.content = data["title"], data["category"], data["content"]
                post.save(update_fields=["title", "category", "content", "updated_at"])
                post.files.exclude(pk__in=file_ids).update(is_removed=True)
                created = False
            PublicResourceFile.objects.filter(pk__in=file_ids).update(post=post, is_removed=False)
            positions = {file_id: index for index, file_id in enumerate(file_ids)}
            for file in files:
                file.position = positions[file.pk]
            PublicResourceFile.objects.bulk_update(files, ["position"])
        post._prefetched_objects_cache = {}
        return post, created

    @extend_schema(request=ResourceWriteSerializer, responses={201: ResourcePostSerializer, 200: ResourcePostSerializer})
    def create(self, request):
        post, created = self._save(request)
        return Response(
            self.get_serializer(post).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @extend_schema(request=ResourceWriteSerializer, responses=ResourcePostSerializer)
    def partial_update(self, request, pk=None):
        post, _ = self._save(request, pk)
        return Response(self.get_serializer(post).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        require_publisher(request)
        with transaction.atomic():
            require_publisher(request, lock=True)
            post = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            post.status = "deleted"
            post.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicResourceUploadView(APIView):
    permission_classes = [TenantResolved]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=inline_serializer("ResourceUpload", fields={"file": serializers.FileField()}), responses={201: ResourceFileSerializer})
    def post(self, request):
        require_publisher(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "첨부 파일을 선택해주세요."})
        filename, extension, content_type = validate_resource_file(upload)
        file_id = uuid.uuid4()
        key = f"landing-public/resources/{request.tenant.id}/{file_id}"
        file = PublicResourceFile.objects.create(
            id=file_id,
            tenant=request.tenant,
            uploaded_by=request.user,
            storage_key=key,
            filename=filename,
            extension=extension if extension in ("pdf", "hwp", "hwpx") else "",
            content_type=content_type,
            size=upload.size,
        )
        try:
            upload_fileobj_to_r2_admin(
                fileobj=upload,
                key=key,
                content_type=content_type,
                content_disposition=resource_content_disposition(filename),
            )
            with transaction.atomic():
                require_publisher(request, lock=True)
                file.is_ready = True
                file.save(update_fields=["is_ready"])
        except Exception:
            # Keep a recoverable pending row if object cleanup itself fails.
            try:
                delete_object_r2_admin(key=key)
                file.delete()
            except Exception:
                pass
            return Response({"detail": "파일을 올리지 못했습니다. 연결을 확인하고 다시 시도해주세요."}, status=503)
        # Conversion failures retain the original and expose a retryable state.
        try:
            file = prepare_reader(file)
        except Exception:
            PublicResourceFile.objects.filter(pk=file.pk).update(reader_status="failed")
            file.reader_status = "failed"
        return Response(ResourceFileSerializer(file).data, status=201)


class PublicResourceFileView(APIView):
    permission_classes = [TenantResolved]

    @extend_schema(auth=[], responses=inline_serializer("ResourceDownloadLink", fields={"url": serializers.URLField(), "expires_in": serializers.IntegerField()}))
    def get(self, request, file_id):
        file = get_object_or_404(
            PublicResourceFile.objects.select_related("post"),
            tenant=request.tenant,
            pk=file_id,
            is_removed=False,
            is_ready=True,
            post__tenant=request.tenant,
            post__status="published",
        )
        try:
            url = generate_presigned_get_url_admin(key=file.storage_key, expires_in=LINK_TTL)
        except Exception:
            return Response(
                {"detail": "다운로드를 준비하지 못했습니다. 다시 시도해주세요."},
                status=503,
                headers={"Cache-Control": "no-store"},
            )
        return Response({"url": url, "expires_in": LINK_TTL}, headers={"Cache-Control": "no-store"})

    @extend_schema(responses={204: None})
    def delete(self, request, file_id):
        require_publisher(request)
        with transaction.atomic():
            require_publisher(request, lock=True)
            file = get_object_or_404(
                PublicResourceFile.objects.select_for_update(),
                tenant=request.tenant,
                pk=file_id,
                uploaded_by=request.user,
                post__isnull=True,
            )
            # Commit the tombstone before external deletion: DB failure after R2
            # deletion must never leave an attachable reference to a missing file.
            file.is_ready = False
            file.save(update_fields=["is_ready"])
        try:
            delete_reader_objects(file)
            delete_object_r2_admin(key=file.storage_key)
            PublicResourceFile.objects.filter(
                pk=file_id,
                tenant=request.tenant,
                uploaded_by=request.user,
                post__isnull=True,
                is_ready=False,
            ).delete()
        except Exception:
            return Response({"detail": "첨부 파일을 정리하지 못했습니다. 다시 시도해주세요."}, status=503)
        return Response(status=204)


class ResourceReaderSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("unprepared", "pending", "ready", "failed", "unsupported"))
    mode = serializers.ChoiceField(choices=("article", "pages"), required=False)
    blocks = serializers.ListField(child=serializers.DictField(), required=False)
    pdf_url = serializers.URLField(required=False)
    pages = serializers.IntegerField(required=False, allow_null=True)
    message = serializers.CharField(required=False)


class PublicResourceReaderView(APIView):
    permission_classes = [TenantResolved]

    def _file(self, request, file_id):
        file = get_object_or_404(PublicResourceFile.objects.select_related("post"),
                               tenant=request.tenant, pk=file_id, is_ready=True, is_removed=False)
        if file.post_id:
            if file.post.tenant_id != request.tenant.id or file.post.status != "published":
                raise Http404
        elif not can_publish(request) or file.uploaded_by_id != request.user.pk:
            raise Http404
        return file

    @extend_schema(auth=[], responses=ResourceReaderSerializer)
    def get(self, request, file_id):
        file = self._file(request, file_id)
        try:
            data = reader_payload(file)
        except Exception:
            return Response({"detail": "본문을 불러오지 못했습니다. 다시 시도해주세요."}, status=503,
                            headers={"Cache-Control": "no-store"})
        return Response(data, headers={"Cache-Control": "no-store"})

    @extend_schema(request=None, responses=ResourceReaderSerializer)
    def post(self, request, file_id):
        require_publisher(request)
        try:
            with transaction.atomic():
                require_publisher(request, lock=True)
                file = prepare_reader(self._file(request, file_id))
            data = reader_payload(file)
        except APIException:
            raise
        except Http404:
            raise
        except Exception:
            return Response({"detail": "본문 준비를 시작하지 못했습니다. 원본은 보존됩니다. 다시 시도해주세요."},
                            status=503, headers={"Cache-Control": "no-store"})
        return Response(data, headers={"Cache-Control": "no-store"})
