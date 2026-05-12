from django.contrib import admin

from .models import PublicBoardPost, PublicReview, PublicPostReply, PublicPostLike, PublicReport, PublicUserBlock


@admin.register(PublicBoardPost)
class PublicBoardPostAdmin(admin.ModelAdmin):
    list_display = (
        "id", "tenant", "title", "category", "author_display_name",
        "status", "external_visible", "is_pinned", "is_hot",
        "like_count", "reply_count", "created_at",
    )
    list_filter = ("tenant", "status", "category", "external_visible", "is_pinned", "is_hot")
    search_fields = ("title", "content", "author_display_name")
    readonly_fields = ("created_at", "updated_at", "view_count", "like_count", "reply_count")
    raw_id_fields = ("author", "moderated_by")


@admin.register(PublicReview)
class PublicReviewAdmin(admin.ModelAdmin):
    list_display = (
        "id", "tenant", "rating", "author_display_name", "grade", "subject",
        "status", "is_pinned", "is_verified",
        "like_count", "reply_count", "created_at",
    )
    list_filter = ("tenant", "status", "rating", "is_pinned", "is_verified")
    search_fields = ("title", "content", "author_display_name")
    readonly_fields = ("created_at", "updated_at", "like_count", "reply_count")
    raw_id_fields = ("author", "reviewed_by")


@admin.register(PublicPostReply)
class PublicPostReplyAdmin(admin.ModelAdmin):
    list_display = (
        "id", "tenant", "target_kind", "target_id",
        "author_display_name", "is_owner_reply", "is_hidden", "created_at",
    )
    list_filter = ("tenant", "target_kind", "is_owner_reply", "is_hidden")
    search_fields = ("content", "author_display_name")
    raw_id_fields = ("author", "parent_reply")


@admin.register(PublicPostLike)
class PublicPostLikeAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "target_kind", "target_id", "user", "created_at")
    list_filter = ("tenant", "target_kind")
    raw_id_fields = ("user",)


@admin.register(PublicReport)
class PublicReportAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "target_kind", "target_id", "reason", "status", "reporter", "created_at")
    list_filter = ("tenant", "status", "target_kind", "reason")
    raw_id_fields = ("reporter", "reviewed_by")


@admin.register(PublicUserBlock)
class PublicUserBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "blocked_user", "blocked_by", "reason", "created_at")
    list_filter = ("tenant",)
    raw_id_fields = ("blocked_user", "blocked_by")
