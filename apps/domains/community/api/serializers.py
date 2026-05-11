from rest_framework import serializers
from apps.domains.community.models import PostEntity, PostMapping, ScopeNode, PostTemplate, PostReply, PostAttachment


class ScopeNodeMinimalSerializer(serializers.ModelSerializer):
    """매핑된 노드 태그용. session이 null(COURSE 노드)일 때 session_title 안전 처리."""
    lecture_title = serializers.CharField(source="lecture.title", read_only=True)
    session_title = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ScopeNode
        fields = ["id", "level", "lecture", "session", "lecture_title", "session_title"]

    def get_session_title(self, obj):
        if not getattr(obj, "session_id", None):
            return None
        session = getattr(obj, "session", None)
        return getattr(session, "title", None) if session else None


class PostMappingSerializer(serializers.ModelSerializer):
    node_detail = ScopeNodeMinimalSerializer(source="node", read_only=True)

    class Meta:
        model = PostMapping
        fields = ["id", "post", "node", "node_detail", "created_at"]


class PostReplySerializer(serializers.ModelSerializer):
    """QnA 답변 조회/생성. question 필드는 프론트 Answer 타입 호환용(post_id)."""
    question = serializers.IntegerField(source="post_id", read_only=True)
    created_by_display = serializers.SerializerMethodField(read_only=True)
    like_count = serializers.SerializerMethodField(read_only=True)
    is_liked = serializers.SerializerMethodField(read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 2026-05-11 보안 리뷰 M2: parent_reply queryset을 tenant + post로 scope.
        # DRF 기본은 PrimaryKeyRelatedField가 global queryset 사용 → cross-tenant ID 존재 여부 timing 누출.
        # context에서 tenant + post 받으면 그에 맞춰 제한. view에서 직접 추가 검증도 유지(defense in depth).
        request = self.context.get("request") if hasattr(self, "context") else None
        tenant = getattr(request, "tenant", None) if request else None
        view = self.context.get("view") if hasattr(self, "context") else None
        if tenant and "parent_reply" in self.fields:
            try:
                post_id = view.kwargs.get("pk") if view else None
                qs = PostReply.objects.filter(tenant=tenant)
                if post_id:
                    qs = qs.filter(post_id=int(post_id))
                self.fields["parent_reply"].queryset = qs
            except (AttributeError, TypeError, ValueError):
                pass  # context 없으면 그대로(view-level 검증으로 차단)

    class Meta:
        model = PostReply
        fields = ["id", "post", "question", "content", "created_by", "created_by_display", "author_role", "created_at", "like_count", "is_liked", "parent_reply"]
        read_only_fields = ["post", "created_by", "created_at"]

    def get_like_count(self, obj):
        # annotation 우선, 미존재 시 count() fallback (단건 조회 path)
        n = getattr(obj, "like_count_anno", None)
        if n is not None:
            return n
        try:
            return obj.likes.count()
        except Exception:
            return 0

    def get_is_liked(self, obj):
        # 2026-05-11 보안 리뷰 H1: 방어적 tenant_id 필터 추가.
        # 현 모델은 obj.likes(=related_name=likes)가 이미 FK 통해 obj의 tenant로 제약되지만,
        # 향후 모델 리팩토링에 견고하도록 명시적 tenant 필터를 둠.
        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None) if request else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        try:
            return obj.likes.filter(user_id=user.id, tenant_id=getattr(obj, "tenant_id", None)).exists()
        except Exception:
            return False

    def get_created_by_display(self, obj):
        author_role = (getattr(obj, "author_role", None) or "").lower()
        created_by = getattr(obj, "created_by", None)
        # 1) 학생 작성인데 FK가 NULL → 하드 삭제된 학생
        if author_role == "student" and created_by is None:
            return "삭제된 학생"
        # 2) author_display_name (저장된 이름)
        display = getattr(obj, "author_display_name", None)
        if display:
            return display
        # 3) Student FK 살아있고 soft-delete 표시
        if created_by:
            if getattr(created_by, "deleted_at", None):
                return "삭제된 학생"
            return getattr(created_by, "name", None)
        # 4) 관리자 (레거시)
        return "관리자"

    def create(self, validated_data):
        post = validated_data.pop("post")
        created_by = validated_data.pop("created_by", None)
        tenant = validated_data.pop("tenant", None)
        author_display_name = validated_data.pop("author_display_name", None)
        author_role = validated_data.pop("author_role", "staff")
        parent_reply = validated_data.pop("parent_reply", None)
        # 추가 sanitize defense — view에서 이미 적용되지만 create path 직접 호출에도 안전 (보안 H2 follow-up).
        from apps.domains.community.services.html_sanitizer import sanitize_html
        safe_content = sanitize_html(validated_data.get("content") or "")
        return PostReply.objects.create(
            post=post,
            tenant_id=tenant.id if tenant else post.tenant_id,
            content=safe_content,
            created_by=created_by,
            author_display_name=author_display_name,
            author_role=author_role,
            parent_reply=parent_reply,
        )


class PostAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostAttachment
        fields = ["id", "original_name", "size_bytes", "content_type", "created_at"]
        read_only_fields = fields


class PostEntitySerializer(serializers.ModelSerializer):
    content = serializers.CharField(allow_blank=True, required=False, default="")

    mappings = PostMappingSerializer(many=True, read_only=True)
    attachments = PostAttachmentSerializer(many=True, read_only=True)
    post_type_label = serializers.SerializerMethodField(read_only=True)
    replies_count = serializers.SerializerMethodField(read_only=True)
    created_by_display = serializers.SerializerMethodField(read_only=True)
    created_by_deleted = serializers.SerializerMethodField(read_only=True)
    like_count = serializers.SerializerMethodField(read_only=True)
    is_liked = serializers.SerializerMethodField(read_only=True)

    def get_like_count(self, obj):
        n = getattr(obj, "like_count_anno", None)
        if n is not None:
            return n
        try:
            return obj.likes.count()
        except Exception:
            return 0

    def get_is_liked(self, obj):
        # 2026-05-11 보안 리뷰 H1: 방어적 tenant_id 필터 추가.
        # 현 모델은 obj.likes(=related_name=likes)가 이미 FK 통해 obj의 tenant로 제약되지만,
        # 향후 모델 리팩토링에 견고하도록 명시적 tenant 필터를 둠.
        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None) if request else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        try:
            return obj.likes.filter(user_id=user.id, tenant_id=getattr(obj, "tenant_id", None)).exists()
        except Exception:
            return False

    def get_post_type_label(self, obj):
        return obj.get_post_type_display() if getattr(obj, "post_type", None) else None

    def get_replies_count(self, obj):
        return getattr(obj, "replies_count", 0) if hasattr(obj, "replies_count") else 0

    def _is_created_by_deleted(self, obj):
        """학생 작성 글의 작성자 학생이 삭제되었는지 (hard-delete 또는 soft-delete).

        author_role='student'이면서 (FK NULL OR deleted_at 존재) → 삭제됨.
        author_role='staff'이거나 미설정이면 관리자/강사 글로 간주.
        """
        author_role = (getattr(obj, "author_role", None) or "").lower()
        created_by = getattr(obj, "created_by", None)
        if author_role == "student":
            if created_by is None:
                return True  # hard-delete
            return bool(getattr(created_by, "deleted_at", None))
        # staff/관리자 글: 삭제 개념 없음
        return False

    def get_created_by_deleted(self, obj):
        return self._is_created_by_deleted(obj)

    def get_created_by_display(self, obj):
        if self._is_created_by_deleted(obj):
            return "삭제된 학생"
        # 1) author_display_name (관리자/학생 모두 저장)
        display = getattr(obj, "author_display_name", None)
        if display:
            return display
        # 2) Student FK
        created_by = getattr(obj, "created_by", None)
        if created_by:
            return getattr(created_by, "name", None)
        # 3) 관리자 글인데 이름 미저장 (레거시)
        return "관리자"

    class Meta:
        model = PostEntity
        fields = [
            "id",
            "tenant",
            "post_type",
            "post_type_label",
            "title",
            "content",
            "category_label",
            "created_by",
            "created_by_display",
            "created_by_deleted",
            "author_role",
            "is_urgent",
            "is_pinned",
            "status",
            "published_at",
            "created_at",
            "replies_count",
            "like_count",
            "is_liked",
            "mappings",
            "attachments",
            "meta",
        ]
        read_only_fields = ["tenant", "created_by", "meta"]


class PostTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostTemplate
        fields = [
            "id",
            "name",
            "title",
            "content",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
