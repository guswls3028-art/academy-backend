import hashlib
import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.domains.community.api.serializers import (
    PostEntitySerializer,
    PostReplySerializer,
    PostAttachmentSerializer,
    BlockTypeSerializer,
    ScopeNodeMinimalSerializer,
    PostTemplateSerializer,
)
from apps.domains.community.selectors import (
    get_posts_for_node,
    get_admin_post_list,
    get_post_by_id,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
    get_notice_posts_for_tenant,
    get_block_types_for_tenant,
    get_empty_block_type_queryset,
    get_scope_nodes_for_tenant,
    get_empty_scope_node_queryset,
)
from apps.domains.community.services import CommunityService
from apps.domains.community.models import PostTemplate, PostReply, BlockType, PostAttachment
from apps.domains.student_app.permissions import get_request_student
from apps.core.permissions import TenantResolvedAndStaff

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_ATTACHMENTS_PER_POST = 10


def _get_tenant_from_request(request):
    """request.tenant 또는 학생 소속 tenant 반환."""
    tenant = getattr(request, "tenant", None)
    if not tenant:
        request_student = get_request_student(request)
        if request_student and getattr(request_student, "tenant", None):
            tenant = request_student.tenant
    return tenant


class PostViewSet(viewsets.ModelViewSet):
    """Post CRUD. tenant from request. list: ?node_id= or admin list."""
    serializer_class = PostEntitySerializer

    def update(self, request, *args, **kwargs):
        """학생은 본인 글만 수정 가능."""
        instance = self.get_object()
        request_student = get_request_student(request)
        if request_student is not None and getattr(instance, "created_by_id", None) != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """학생은 본인 글만 삭제 가능."""
        instance = self.get_object()
        request_student = get_request_student(request)
        if request_student is not None and getattr(instance, "created_by_id", None) != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        """단건 조회: 학생은 (1) 공지(block_type code=notice) 또는 (2) 본인 작성 글만 허용."""
        tenant = _get_tenant_from_request(request)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        pk = int(kwargs.get("pk", 0))
        post = get_post_by_id(tenant, pk)
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        request_student = get_request_student(request)
        if request_student is not None:
            is_notice = getattr(post.block_type, "code", None) and str(post.block_type.code).strip().lower() == "notice"
            is_own = getattr(post, "created_by_id", None) == request_student.id
            if not is_notice and not is_own:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(post)
        return Response(serializer.data)

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            request_student = get_request_student(self.request)
            if request_student and getattr(request_student, "tenant", None):
                tenant = request_student.tenant
        if not tenant:
            return get_empty_post_queryset()
        raw = self.request.query_params.get("node_id")
        try:
            node_id = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            node_id = None
        if node_id is not None:
            return get_posts_for_node(tenant, node_id, include_inherited=True)
        qs = get_all_posts_for_tenant(tenant)
        # 학생 요청 시 node_id 없으면 본인 작성 글만 반환 (학생 앱 "내 질문" 목록)
        request_student = get_request_student(self.request)
        if request_student is not None:
            qs = qs.filter(created_by=request_student)
        return qs

    def list(self, request, *args, **kwargs):
        # 학생 "내 질문" 목록: node_id 없이 호출 시 페이지네이션 없이 전체 반환 (학생 앱에서 한 번에 조회)
        request_student = get_request_student(request)
        if request_student is not None and request.query_params.get("node_id") in (None, ""):
            qs = self.filter_queryset(self.get_queryset())
            serializer = self.get_serializer(qs, many=True)
            return Response(serializer.data)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="notices")
    def notices(self, request):
        """GET /community/posts/notices/ — 학생앱·관리자 동일: block_type code=notice 인 공지 목록."""
        tenant = getattr(request, "tenant", None)
        request_student = get_request_student(request)
        if not tenant and request_student and getattr(request_student, "tenant", None):
            tenant = request_student.tenant
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        qs = get_notice_posts_for_tenant(tenant)
        try:
            page_size = min(int(request.query_params.get("page_size") or 50), 200)
        except (TypeError, ValueError):
            page_size = 50
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        offset = (page - 1) * page_size
        page_qs = qs[offset : offset + page_size]
        serializer = self.get_serializer(page_qs, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request, "tenant", None)
        request_student = get_request_student(request)
        # tenant 미설정 시 학생 소속 tenant로 폴백 (호스트 기반 테넌트 해석 실패 시 403 방지)
        if not tenant and request_student and getattr(request_student, "tenant", None):
            tenant = request_student.tenant
        if not tenant:
            return Response(
                {"detail": "tenant required", "code": "tenant_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        node_ids = request.data.get("node_ids") or []
        created_by = serializer.validated_data.get("created_by")
        if request_student is not None:
            created_by = request_student
        elif created_by is None and getattr(request.user, "student_profile", None):
            created_by = request.user.student_profile
        # QnA는 작성자(created_by) 필수. 프로필 로드 전 제출 시 null 저장 방지(배포에서 "질문 등록 안 됨" 원인).
        block_type = serializer.validated_data.get("block_type")
        if block_type is not None:
            code = getattr(block_type, "code", None) or ""
            if str(code).strip().lower() == "qna" and created_by is None:
                return Response(
                    {
                        "detail": "프로필을 불러온 후 다시 시도해 주세요.",
                        "code": "profile_required",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        data = {
            "block_type": serializer.validated_data["block_type"],
            "title": serializer.validated_data["title"],
            "content": serializer.validated_data["content"],
            "category_label": request.data.get("category_label"),
            "created_by": created_by,
        }
        svc = CommunityService(tenant)
        post = svc.create_post(data, node_ids)
        return Response(PostEntitySerializer(post).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="nodes")
    def update_nodes(self, request, pk=None):
        """PATCH /posts/:id/nodes/ body: { node_ids: [1,2,3] }"""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        node_ids = request.data.get("node_ids") or []
        svc = CommunityService(tenant)
        svc.update_post_nodes(int(pk), node_ids)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PostEntitySerializer(post).data)

    @action(detail=True, methods=["get", "post"], url_path="replies")
    def replies(self, request, pk=None):
        """GET/POST /posts/:id/replies/ — 답변 목록 조회, 답변 등록(선생/관리자)."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            qs = PostReply.objects.filter(post=post, tenant=tenant).select_related("created_by").order_by("created_at")
            serializer = PostReplySerializer(qs, many=True)
            return Response(serializer.data)

        # POST: 답변 등록 (content만 필수)
        serializer = PostReplySerializer(data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        # created_by: 학생이면 student, 아니면 staff에서 가져옴
        created_by = None
        request_student = get_request_student(request)
        if request_student is not None:
            created_by = request_student
        elif hasattr(request.user, "staff"):
            created_by = request.user.staff
        reply = serializer.save(post=post, tenant=tenant, created_by=created_by)
        return Response(PostReplySerializer(reply).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="attachments")
    def upload_attachments(self, request, pk=None):
        """POST /posts/:id/attachments/ — 첨부파일 업로드 (multipart)."""
        tenant = _get_tenant_from_request(request)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # 학생은 본인 글에만 첨부 가능
        request_student = get_request_student(request)
        if request_student is not None and post.created_by_id != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)

        files = request.FILES.getlist("files")
        if not files:
            return Response({"detail": "파일이 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        existing_count = PostAttachment.objects.filter(post=post, tenant=tenant).count()
        if existing_count + len(files) > MAX_ATTACHMENTS_PER_POST:
            return Response(
                {"detail": f"첨부파일은 최대 {MAX_ATTACHMENTS_PER_POST}개까지 가능합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

        created = []
        for f in files:
            if f.size > MAX_ATTACHMENT_SIZE:
                return Response(
                    {"detail": f"파일 '{f.name}'이(가) 50MB를 초과합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            name_hash = hashlib.md5(f.name.encode()).hexdigest()[:8]
            r2_key = f"tenants/{tenant.id}/community/posts/{post.id}/{name_hash}_{f.name}"

            upload_fileobj_to_r2_storage(
                fileobj=f,
                key=r2_key,
                content_type=f.content_type or "application/octet-stream",
            )
            att = PostAttachment.objects.create(
                tenant=tenant,
                post=post,
                r2_key=r2_key,
                original_name=f.name,
                size_bytes=f.size,
                content_type=f.content_type or "application/octet-stream",
            )
            created.append(att)
            logger.info("PostAttachment created: post=%s, file=%s, key=%s", post.id, f.name, r2_key)

        serializer = PostAttachmentSerializer(created, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path=r"attachments/(?P<att_id>[^/.]+)/download")
    def download_attachment(self, request, pk=None, att_id=None):
        """GET /posts/:id/attachments/:att_id/download/ — presigned download URL 리다이렉트."""
        tenant = _get_tenant_from_request(request)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # 학생 접근 제어: 공지·자료실·게시판 글은 허용, 그 외는 본인 글만
        request_student = get_request_student(request)
        if request_student is not None:
            bt_code = (getattr(post.block_type, "code", None) or "").strip().lower()
            is_public = bt_code in ("notice", "materials", "board")
            is_own = post.created_by_id == request_student.id
            if not is_public and not is_own:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            att = PostAttachment.objects.get(id=int(att_id), post=post, tenant=tenant)
        except (PostAttachment.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        url = generate_presigned_get_url_storage(
            key=att.r2_key,
            expires_in=3600,
            filename=att.original_name,
            content_type=att.content_type or None,
        )
        return Response({"url": url, "original_name": att.original_name})

    @action(detail=True, methods=["delete"], url_path=r"attachments/(?P<att_id>[^/.]+)")
    def delete_attachment(self, request, pk=None, att_id=None):
        """DELETE /posts/:id/attachments/:att_id/ — 첨부파일 삭제."""
        tenant = _get_tenant_from_request(request)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # 학생은 본인 글의 첨부파일만 삭제 가능
        request_student = get_request_student(request)
        if request_student is not None and post.created_by_id != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)

        try:
            att = PostAttachment.objects.get(id=int(att_id), post=post, tenant=tenant)
        except (PostAttachment.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        from apps.infrastructure.storage.r2 import delete_object_r2_storage
        try:
            delete_object_r2_storage(key=att.r2_key)
        except Exception:
            logger.warning("R2 delete failed for key=%s, removing DB record anyway", att.r2_key)
        att.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["patch", "delete"], url_path=r"replies/(?P<reply_id>[^/.]+)")
    def reply_detail(self, request, pk=None, reply_id=None):
        """PATCH/DELETE /posts/:id/replies/:reply_id/ — 답변 수정/삭제."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            reply = PostReply.objects.get(post=post, id=int(reply_id), tenant=tenant)
        except (PostReply.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # 학생은 본인 답변만 수정/삭제 가능
        request_student = get_request_student(request)
        if request_student is not None and reply.created_by_id != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "DELETE":
            reply.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        # PATCH
        serializer = PostReplySerializer(reply, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AdminPostViewSet(viewsets.GenericViewSet):
    """Admin list with filters. block_type_id, lecture_id, page, page_size."""
    serializer_class = PostEntitySerializer

    def list(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        def _int_or_none(val):
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        block_type_id = _int_or_none(request.query_params.get("block_type_id"))
        lecture_id = _int_or_none(request.query_params.get("lecture_id"))
        try:
            page = int(request.query_params.get("page") or 1)
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page, page_size = 1, 20
        qs, total = get_admin_post_list(
            tenant,
            block_type_id=block_type_id,
            lecture_id=lecture_id,
            page=page,
            page_size=page_size,
        )
        serializer = self.get_serializer(qs, many=True)
        return Response({"results": serializer.data, "count": total})

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        return get_all_posts_for_tenant(tenant)


class BlockTypeViewSet(viewsets.ModelViewSet):
    """블록 유형 CRUD. 커스텀 유형 생성/수정/삭제. tenant에 하나도 없으면 기본 QnA 유형 자동 생성."""
    serializer_class = BlockTypeSerializer
    permission_classes = [TenantResolvedAndStaff]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_block_type_queryset()
        return get_block_types_for_tenant(tenant)

    def list(self, request, *args, **kwargs):
        """목록 조회 시 tenant에 블록 유형이 없으면 기본 QnA·공지 유형을 한 번만 생성 후 반환."""
        tenant = getattr(request, "tenant", None)
        if tenant:
            qs = get_block_types_for_tenant(tenant)
            if not qs.exists():
                BlockType.objects.get_or_create(
                    tenant=tenant,
                    code="qna",
                    defaults={"label": "QnA", "order": 1},
                )
                BlockType.objects.get_or_create(
                    tenant=tenant,
                    code="notice",
                    defaults={"label": "공지", "order": 2},
                )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        data = dict(serializer.validated_data)
        code = (data.pop("code", None) or "").strip()[:32]
        if not code:
            import re
            code = re.sub(r"[^a-zA-Z0-9가-힣_]", "_", data.get("label", ""))[:32] or "CUSTOM"
        serializer.save(tenant=tenant, code=code, **data)


class PostTemplateViewSet(viewsets.ModelViewSet):
    """글 양식 CRUD. 자주 쓰는 제목/본문/유형 저장·불러오기."""
    serializer_class = PostTemplateSerializer
    permission_classes = [TenantResolvedAndStaff]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PostTemplate.objects.none()
        return (
            PostTemplate.objects.filter(tenant=tenant)
            .select_related("block_type")
            .order_by("order", "id")
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            serializer.save(tenant=tenant)


class ScopeNodeViewSet(viewsets.ReadOnlyModelViewSet):
    """ScopeNode list for tree. Filter by tenant (from request). Pagination disabled so frontend gets full list."""
    serializer_class = ScopeNodeMinimalSerializer
    pagination_class = None

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_scope_node_queryset()
        return get_scope_nodes_for_tenant(tenant)
