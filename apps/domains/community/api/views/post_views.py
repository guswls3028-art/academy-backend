import hashlib
import logging

from django.db import transaction
from apps.domains.community.services.html_sanitizer import sanitize_html
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.domains.community.api.serializers import (
    PostEntitySerializer,
    PostReplySerializer,
    PostAttachmentSerializer,
)
from apps.domains.community.selectors import (
    get_posts_for_node,
    get_post_by_id,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
    get_posts_by_type_for_tenant,
)
from apps.domains.community.services import CommunityService
from apps.domains.community.models import PostReply, PostAttachment
from apps.domains.student_app.permissions import get_request_student
from apps.core.permissions import TenantResolvedAndMember

from ._common import (
    _get_tenant_from_request,
    MAX_ATTACHMENT_SIZE,
    MAX_ATTACHMENTS_PER_POST,
)

logger = logging.getLogger(__name__)


class PostViewSet(viewsets.ModelViewSet):
    """Post CRUD. tenant from request. list: ?node_id= or admin list."""
    serializer_class = PostEntitySerializer
    permission_classes = [TenantResolvedAndMember]

    def update(self, request, *args, **kwargs):
        """학생은 본인 글만 수정 가능. 학부모는 수정 불가."""
        # 학부모 write 차단
        if getattr(request.user, "parent_profile", None) is not None:
            return Response(
                {"detail": "학부모 계정은 글 수정이 제한됩니다.", "code": "parent_read_only"},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = self.get_object()
        request_student = get_request_student(request)
        if request_student is not None and getattr(instance, "created_by_id", None) != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """학생은 본인 글만 삭제 가능. 학부모는 삭제 불가."""
        # 학부모 write 차단
        if getattr(request.user, "parent_profile", None) is not None:
            return Response(
                {"detail": "학부모 계정은 글 삭제가 제한됩니다.", "code": "parent_read_only"},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = self.get_object()
        request_student = get_request_student(request)
        if request_student is not None and getattr(instance, "created_by_id", None) != request_student.id:
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        """단건 조회: 학생/학부모는 published 공개 타입 또는 본인 작성 글만 허용."""
        tenant = _get_tenant_from_request(request)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        pk = int(kwargs.get("pk", 0))
        post = get_post_by_id(tenant, pk)
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # staff가 아닌 사용자: published만 조회 가능 + 공개 타입 또는 본인 글
        if not self._is_staff_request(request):
            # unpublished 차단 (본인 글은 draft라도 볼 수 있음)
            request_student = get_request_student(request)
            is_own = request_student is not None and getattr(post, "created_by_id", None) == request_student.id
            if not is_own and getattr(post, "status", "") != "published":
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            # 비공개 타입(qna/counsel)은 본인 글만
            if not is_own:
                from apps.domains.community.models.post import STUDENT_PUBLIC_POST_TYPES
                is_public = getattr(post, "post_type", "") in STUDENT_PUBLIC_POST_TYPES
                if not is_public:
                    return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(post)
        return Response(serializer.data)

    def _is_staff_request(self, request) -> bool:
        """staff/admin 여부를 TenantMembership 역할로 판단. 학부모는 staff가 아님."""
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return False
        from apps.core.models import TenantMembership
        return TenantMembership.objects.filter(
            tenant=tenant, user=user, is_active=True,
            role__in=["owner", "admin", "staff", "teacher"],
        ).exists()

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        request_student = get_request_student(self.request)
        is_staff = self._is_staff_request(self.request)
        raw = self.request.query_params.get("node_id")
        try:
            node_id = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            node_id = None
        if node_id is not None:
            qs = get_posts_for_node(tenant, node_id, include_inherited=True, include_unpublished=is_staff)
            if request_student is not None:
                from apps.domains.community.models.post import STUDENT_PUBLIC_POST_TYPES
                qs = qs.filter(
                    Q(post_type__in=STUDENT_PUBLIC_POST_TYPES) |
                    Q(created_by=request_student)
                )
        else:
            qs = get_all_posts_for_tenant(tenant, include_unpublished=is_staff)
            # 학생 요청 시 node_id 없으면 본인 작성 글만 반환 (학생 앱 "내 질문" 목록)
            if request_student is not None:
                qs = qs.filter(created_by=request_student)

        # F4: post_type server-side filter
        from apps.domains.community.models.post import VALID_POST_TYPES
        post_type_param = (self.request.query_params.get("post_type") or "").strip().lower()
        if post_type_param:
            if post_type_param not in VALID_POST_TYPES:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"post_type": f"허용되지 않는 타입입니다: {post_type_param}"})
            qs = qs.filter(post_type=post_type_param)

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
        """GET /community/posts/notices/ — 공지 목록."""
        return self._list_by_type(request, "notice")

    def _list_by_type(self, request, post_type: str):
        """
        공용: post_type별 목록 (notices/board/materials 공통).
        학생 요청 시 가시성 정책 적용:
          - mapping 없음(전체글) → 보임
          - mapping 있음 → 학생이 수강 중인 강의/세션 node에 매핑된 것만 보임
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        qs = get_posts_by_type_for_tenant(tenant, post_type)

        # 학생이면 수강 기반 스코프 필터링
        request_student = get_request_student(request)
        if request_student is not None:
            from apps.domains.enrollment.models import Enrollment
            from apps.domains.community.models import ScopeNode, PostMapping
            enrolled_lecture_ids = set(
                Enrollment.objects.filter(
                    tenant=tenant, student=request_student, status="ACTIVE"
                ).values_list("lecture_id", flat=True)
            )
            # 학생이 볼 수 있는 node: 수강 강의의 COURSE + SESSION 노드
            visible_node_ids = set(
                ScopeNode.objects.filter(
                    tenant=tenant, lecture_id__in=enrolled_lecture_ids
                ).values_list("id", flat=True)
            )
            # mapping 없는 글(전체글) OR 학생의 visible node에 매핑된 글
            scoped_post_ids = set(
                PostMapping.objects.filter(
                    node_id__in=visible_node_ids
                ).values_list("post_id", flat=True)
            )
            qs = qs.filter(
                Q(mappings__isnull=True) | Q(id__in=scoped_post_ids)
            ).distinct()

        try:
            page_size = min(int(request.query_params.get("page_size") or 50), 200)
        except (TypeError, ValueError):
            page_size = 50
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        total = qs.count()
        offset = (page - 1) * page_size
        page_qs = qs[offset : offset + page_size]
        serializer = self.get_serializer(page_qs, many=True)
        return Response({
            "count": total,
            "results": serializer.data,
        })

    @action(detail=False, methods=["get"], url_path="board")
    def board(self, request):
        """GET /community/posts/board/ — 게시판 목록."""
        return self._list_by_type(request, "board")

    @action(detail=False, methods=["get"], url_path="materials")
    def materials(self, request):
        """GET /community/posts/materials/ — 자료실 목록."""
        return self._list_by_type(request, "materials")

    def create(self, request, *args, **kwargs):
        # 학부모 write 차단 — 학부모는 읽기 전용
        if getattr(request.user, "parent_profile", None) is not None:
            return Response(
                {"detail": "학부모 계정은 글 작성이 제한됩니다.", "code": "parent_read_only"},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request, "tenant", None)
        request_student = get_request_student(request)
        if not tenant:
            return Response(
                {"detail": "tenant required", "code": "tenant_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        node_ids = request.data.get("node_ids") or []
        if not isinstance(node_ids, list):
            return Response({"detail": "node_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        created_by = serializer.validated_data.get("created_by")
        if request_student is not None:
            created_by = request_student
        elif created_by is None and getattr(request.user, "student_profile", None):
            created_by = request.user.student_profile

        # Resolve post_type: from request data, or fall back to block_type.code
        post_type = (request.data.get("post_type") or "").strip().lower()
        block_type = serializer.validated_data.get("block_type")
        if not post_type and block_type is not None:
            # Backward compat: derive post_type from block_type.code
            code = (getattr(block_type, "code", None) or "").strip().lower()
            valid_types = {"notice", "board", "materials", "qna", "counsel"}
            post_type = code if code in valid_types else "board"
        if not post_type:
            post_type = "board"

        # QnA는 작성자(created_by) 필수. 프로필 로드 전 제출 시 null 저장 방지.
        if post_type == "qna" and created_by is None:
            return Response(
                {
                    "detail": "프로필을 불러온 후 다시 시도해 주세요.",
                    "code": "profile_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        # 작성자 정보 resolve
        author_display_name = None
        author_role = "staff"
        if created_by is not None:
            # 학생 작성
            author_display_name = getattr(created_by, "name", None)
            author_role = "student"
        elif request.user and request.user.is_authenticated:
            # 관리자/강사 작성
            staff = getattr(request.user, "staff", None) or getattr(request.user, "staff_profile", None)
            if staff and getattr(staff, "name", None):
                author_display_name = staff.name
            elif getattr(request.user, "first_name", None) or getattr(request.user, "last_name", None):
                author_display_name = f"{request.user.last_name}{request.user.first_name}".strip() or None

        # Sanitize HTML content server-side
        raw_content = serializer.validated_data["content"]
        safe_content = sanitize_html(raw_content) if raw_content else ""

        data = {
            "post_type": post_type,
            "block_type": block_type,
            "title": serializer.validated_data["title"],
            "content": safe_content,
            "category_label": request.data.get("category_label"),
            "created_by": created_by,
            "author_display_name": author_display_name,
            "author_role": author_role,
            "is_urgent": bool(request.data.get("is_urgent", False)),
            "is_pinned": bool(request.data.get("is_pinned", False)),
            "status": request.data.get("status", "published"),
            "published_at": request.data.get("published_at") or None,
        }
        svc = CommunityService(tenant)
        post = svc.create_post(data, node_ids)
        return Response(self.get_serializer(post).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        """PATCH /posts/:id/ — sanitize content on update, enforce parent read-only."""
        request = self.request
        # 학부모 write 차단
        if getattr(request.user, "parent_profile", None) is not None:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("학부모 계정은 수정이 제한됩니다.")
        # Content sanitization
        if "content" in serializer.validated_data:
            serializer.validated_data["content"] = sanitize_html(
                serializer.validated_data["content"]
            )
        serializer.save()

    @action(detail=True, methods=["patch"], url_path="nodes")
    def update_nodes(self, request, pk=None):
        """PATCH /posts/:id/nodes/ body: { node_ids: [1,2,3] }"""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        node_ids = request.data.get("node_ids") or []
        if not isinstance(node_ids, list):
            return Response({"detail": "node_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        svc = CommunityService(tenant)
        svc.update_post_nodes(int(pk), node_ids)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(post).data)

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
        # 학생은 공개 타입(notice/board/materials) 또는 본인 글에만 답변 가능
        request_student = get_request_student(request)
        if request_student is not None:
            from apps.domains.community.models.post import STUDENT_PUBLIC_POST_TYPES
            is_public = getattr(post, "post_type", "") in STUDENT_PUBLIC_POST_TYPES
            is_own = getattr(post, "created_by_id", None) == request_student.id
            if not is_public and not is_own:
                return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)

        serializer = PostReplySerializer(data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)

        created_by = None
        author_display_name = None
        author_role = "staff"
        if request_student is not None:
            created_by = request_student
            author_display_name = getattr(request_student, "name", None)
            author_role = "student"
        else:
            # Staff: 이름 resolve
            staff = getattr(request.user, "staff", None) or getattr(request.user, "staff_profile", None)
            if staff and getattr(staff, "name", None):
                author_display_name = staff.name
            elif request.user:
                author_display_name = f"{request.user.last_name}{request.user.first_name}".strip() or None

        # 🔐 XSS 방지: 댓글 content sanitize
        content = serializer.validated_data.get("content", "")
        if content:
            serializer.validated_data["content"] = sanitize_html(content)

        reply = serializer.save(
            post=post, tenant=tenant, created_by=created_by,
            author_display_name=author_display_name, author_role=author_role,
        )
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

        # Step 1: Pre-validate ALL file sizes before any upload
        for f in files:
            if f.size > MAX_ATTACHMENT_SIZE:
                return Response(
                    {"detail": f"파일 '{f.name}'이(가) 50MB를 초과합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage, delete_object_r2_storage

        # Step 2: R2 uploads (outside atomic — external I/O)
        uploaded = []  # list of (r2_key, original_name, size_bytes, content_type)
        try:
            for f in files:
                name_hash = hashlib.md5(f.name.encode()).hexdigest()[:8]
                r2_key = f"tenants/{tenant.id}/community/posts/{post.id}/{name_hash}_{f.name}"
                upload_fileobj_to_r2_storage(
                    fileobj=f,
                    key=r2_key,
                    content_type=f.content_type or "application/octet-stream",
                )
                uploaded.append((r2_key, f.name, f.size, f.content_type))

            # Step 3: DB creates (inside atomic)
            with transaction.atomic():
                created = []
                for r2_key, fname, fsize, ftype in uploaded:
                    att = PostAttachment.objects.create(
                        tenant=tenant,
                        post=post,
                        r2_key=r2_key,
                        original_name=fname,
                        size_bytes=fsize,
                        content_type=ftype or "application/octet-stream",
                    )
                    created.append(att)
                    logger.info("PostAttachment created: post=%s, file=%s, key=%s", post.id, fname, r2_key)

            # Q&A 이미지 첨부 시 자동 매치업 검색 디스패치
            if post.post_type == "qna":
                _dispatch_qna_matchup(post, created, tenant)

            serializer = PostAttachmentSerializer(created, many=True)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception:
            # Step 4: Best-effort R2 cleanup on failure
            for r2_key, *_ in uploaded:
                try:
                    delete_object_r2_storage(key=r2_key)
                except Exception:
                    logger.warning("R2 orphan cleanup failed: key=%s", r2_key)
            raise

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
            from apps.domains.community.models.post import STUDENT_PUBLIC_POST_TYPES
            is_public = getattr(post, "post_type", "") in STUDENT_PUBLIC_POST_TYPES
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
        # 🔐 XSS 방지: 댓글 수정 시에도 content sanitize
        content = serializer.validated_data.get("content")
        if content:
            serializer.validated_data["content"] = sanitize_html(content)
        serializer.save()
        return Response(serializer.data)


def _dispatch_qna_matchup(post, attachments, tenant):
    """Q&A 이미지 첨부 시 자동 매치업 검색 디스패치."""
    image_atts = [a for a in attachments if (a.content_type or "").startswith("image/")]
    if not image_atts:
        return

    att = image_atts[0]  # 첫 번째 이미지만 검색

    try:
        from apps.domains.ai.gateway import dispatch_job
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        download_url = generate_presigned_get_url_storage(key=att.r2_key, expires_in=3600)
        result = dispatch_job(
            job_type="matchup_search_qna",
            payload={
                "download_url": download_url,
                "post_id": str(post.id),
                "attachment_id": str(att.id),
                "r2_key": att.r2_key,
                "tenant_id": str(tenant.id),
            },
            tenant_id=str(tenant.id),
            source_domain="community_qna",
            source_id=str(post.id),
        )
        logger.info(
            "QNA_MATCHUP_DISPATCHED | post_id=%s | att_id=%s | ok=%s",
            post.id, att.id, result.get("ok") if isinstance(result, dict) else True,
        )
    except Exception:
        logger.warning("QNA_MATCHUP_DISPATCH_FAILED | post_id=%s", post.id, exc_info=True)
