from django.contrib import admin
from .models import (
    BoardCategory,
    BoardPost,
    BoardAttachment,
    BoardReadStatus,
)


@admin.register(BoardCategory)
class BoardCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "lecture", "name", "order")
    list_display_links = ("id", "name")
    list_filter = ("lecture",)
    search_fields = ("name",)
    ordering = ("lecture", "order")


@admin.register(BoardPost)
class BoardPostAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "lecture", "category", "created_by", "created_at")
    list_display_links = ("id", "title")
    list_filter = ("lecture", "category")
    search_fields = ("title", "content")
    ordering = ("-created_at",)


@admin.register(BoardAttachment)
class BoardAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "file")


@admin.register(BoardReadStatus)
class BoardReadStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "enrollment", "checked_at")
    list_display_links = ("id", "post")
    list_filter = ("post__lecture",)
    search_fields = ("enrollment__student__name",)
    ordering = ("-checked_at",)
