from rest_framework import serializers
from apps.domains.community.models import PostEntity, PostMapping, ScopeNode, BlockType, PostTemplate, PostReply, PostAttachment


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

    class Meta:
        model = PostReply
        fields = ["id", "post", "question", "content", "created_by", "created_by_display", "created_at"]
        read_only_fields = ["post", "created_by", "created_at"]

    def get_created_by_display(self, obj):
        if not getattr(obj, "created_by_id", None):
            return None
        created_by = getattr(obj, "created_by", None)
        return getattr(created_by, "name", None) if created_by else None

    def create(self, validated_data):
        post = validated_data.pop("post")
        created_by = validated_data.pop("created_by", None)
        tenant = validated_data.pop("tenant", None)
        return PostReply.objects.create(
            post=post,
            tenant_id=tenant.id if tenant else post.tenant_id,
            content=validated_data["content"],
            created_by=created_by,
        )


class PostAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostAttachment
        fields = ["id", "original_name", "size_bytes", "content_type", "created_at"]
        read_only_fields = fields


class PostEntitySerializer(serializers.ModelSerializer):
    mappings = PostMappingSerializer(many=True, read_only=True)
    attachments = PostAttachmentSerializer(many=True, read_only=True)
    block_type_label = serializers.CharField(source="block_type.label", read_only=True)
    replies_count = serializers.SerializerMethodField(read_only=True)
    created_by_display = serializers.SerializerMethodField(read_only=True)
    created_by_deleted = serializers.SerializerMethodField(read_only=True)

    def get_replies_count(self, obj):
        return getattr(obj, "replies_count", 0) if hasattr(obj, "replies_count") else 0

    def _is_created_by_deleted(self, obj):
        created_by = getattr(obj, "created_by", None)
        if created_by is None:
            return True
        return bool(getattr(created_by, "deleted_at", None))

    def get_created_by_deleted(self, obj):
        return self._is_created_by_deleted(obj)

    def get_created_by_display(self, obj):
        if self._is_created_by_deleted(obj):
            return "삭제된 학생입니다."
        created_by = getattr(obj, "created_by", None)
        return getattr(created_by, "name", None) if created_by else None

    class Meta:
        model = PostEntity
        fields = [
            "id",
            "tenant",
            "block_type",
            "block_type_label",
            "title",
            "content",
            "category_label",
            "created_by",
            "created_by_display",
            "created_by_deleted",
            "created_at",
            "replies_count",
            "mappings",
            "attachments",
        ]
        read_only_fields = ["tenant"]


class BlockTypeSerializer(serializers.ModelSerializer):
    code = serializers.CharField(max_length=32, required=False, allow_blank=True)

    class Meta:
        model = BlockType
        fields = ["id", "code", "label", "order"]
        read_only_fields = ["id"]

    def validate_code(self, value):
        if not value or not value.strip():
            return value
        value = value.strip()[:32]
        if not value:
            raise serializers.ValidationError("code는 1자 이상 필요합니다.")
        return value

    def validate_label(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("표시명을 입력하세요.")
        return value.strip()[:64]


class PostTemplateSerializer(serializers.ModelSerializer):
    block_type_label = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PostTemplate
        fields = [
            "id",
            "name",
            "block_type",
            "block_type_label",
            "title",
            "content",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_block_type_label(self, obj):
        return obj.block_type.label if obj.block_type_id else None
