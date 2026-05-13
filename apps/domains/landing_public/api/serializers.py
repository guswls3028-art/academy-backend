from rest_framework import serializers

from ..models import PublicBoardPost, PublicPostLike, PublicPostReply, PublicReview


_STAFF_ROLES = {"owner", "admin", "staff", "teacher", "assistant"}


def _resolve_display_name(user, fallback: str = "") -> str:
    """User → 화면 노출용 이름. is_anonymous 처리는 호출측 책임."""
    if not user:
        return fallback or "익명"
    name = getattr(user, "name", None) or getattr(user, "first_name", None) or getattr(user, "username", None)
    return name or fallback or "익명"


def _resolve_role(user, tenant) -> str:
    """User → 작성 당시 역할 (student/parent/teacher/owner/admin).
    멤버십 우선, fallback은 user.role.
    """
    if not user:
        return ""
    try:
        from academy.adapters.db.django import repositories_core as core_repo
        m = core_repo.membership_get(tenant=tenant, user=user) if tenant else None
        if m and getattr(m, "role", None):
            return str(m.role).lower()
    except Exception:
        pass
    role = getattr(user, "role", "") or ""
    return str(role).lower()


def _is_staff_role(role: str) -> bool:
    return (role or "").lower() in _STAFF_ROLES


class PublicBoardPostListSerializer(serializers.ModelSerializer):
    """list/preview용 — content 제외, 메타 + 카운트만."""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PublicBoardPost
        fields = (
            "id", "title", "category", "cover_image_url",
            "display_name", "author_role", "is_anonymous",
            "is_pinned", "is_hot",
            "like_count", "reply_count", "view_count",
            "external_visible", "status",
            "created_at", "updated_at",
        )

    def get_display_name(self, obj: PublicBoardPost) -> str:
        if obj.is_anonymous:
            return "익명"
        return obj.author_display_name or "익명"


class PublicBoardPostDetailSerializer(PublicBoardPostListSerializer):
    """detail — content 포함."""
    is_owner_or_author = serializers.SerializerMethodField()

    class Meta(PublicBoardPostListSerializer.Meta):
        fields = PublicBoardPostListSerializer.Meta.fields + (
            "content", "meta", "is_owner_or_author",
        )

    def get_is_owner_or_author(self, obj: PublicBoardPost) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if obj.author_id == user.id:
            return True
        role = self.context.get("viewer_role", "")
        return _is_staff_role(role)


class PublicBoardPostWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicBoardPost
        fields = ("title", "content", "category", "cover_image_url", "is_anonymous", "meta")


class PublicReviewListSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PublicReview
        fields = (
            "id", "rating", "title",
            "display_name", "author_role", "is_anonymous",
            "grade", "subject", "enrollment_months",
            "cover_image_url",
            "is_pinned", "is_verified", "status",
            "like_count", "reply_count",
            "created_at", "updated_at",
        )

    def get_display_name(self, obj: PublicReview) -> str:
        if obj.is_anonymous:
            return "익명"
        return obj.author_display_name or "익명"


class PublicReviewDetailSerializer(PublicReviewListSerializer):
    class Meta(PublicReviewListSerializer.Meta):
        fields = PublicReviewListSerializer.Meta.fields + ("content", "photos")


class PublicReviewWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicReview
        fields = (
            "rating", "title", "content",
            "grade", "subject", "enrollment_months",
            "cover_image_url", "photos", "is_anonymous",
        )

    def validate_rating(self, value: int) -> int:
        if value < 1 or value > 5:
            raise serializers.ValidationError("평점은 1~5 사이여야 합니다.")
        return value

    def validate_photos(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("photos는 URL 리스트여야 합니다.")
        if len(value) > 8:
            raise serializers.ValidationError("사진은 최대 8장까지 첨부 가능합니다.")
        return value


class PublicReviewModerateSerializer(serializers.ModelSerializer):
    """학원장 모더레이션 전용 필드 (승인/거절/숨김/핀/검증)."""
    class Meta:
        model = PublicReview
        fields = ("status", "is_pinned", "is_verified")


class PublicPostReplySerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = PublicPostReply
        fields = (
            "id", "target_kind", "target_id",
            "display_name", "author_role", "is_anonymous", "is_owner_reply",
            "content", "parent_reply",
            "is_hidden", "like_count", "created_at",
            "is_mine",
        )
        read_only_fields = ("is_owner_reply", "is_hidden", "like_count", "is_mine")

    def get_display_name(self, obj: PublicPostReply) -> str:
        if obj.is_anonymous:
            return "익명"
        return obj.author_display_name or "익명"

    def get_is_mine(self, obj: PublicPostReply) -> bool:
        """현재 viewer가 본 댓글 작성자 본인인가. frontend에서 '내 댓글 삭제' 버튼 노출용.
        author_id 자체를 노출하지 않고 boolean 만 — 익명/타인 식별 정보 누출 회피."""
        request = self.context.get("request") if hasattr(self, "context") else None
        if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
            return False
        return obj.author_id == request.user.id


__all__ = [
    "PublicBoardPostListSerializer",
    "PublicBoardPostDetailSerializer",
    "PublicBoardPostWriteSerializer",
    "PublicReviewListSerializer",
    "PublicReviewDetailSerializer",
    "PublicReviewWriteSerializer",
    "PublicReviewModerateSerializer",
    "PublicPostReplySerializer",
    "_resolve_display_name",
    "_resolve_role",
    "_is_staff_role",
]
