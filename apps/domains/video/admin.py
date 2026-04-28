from django.contrib import admin
from .models import (
    Video,
    VideoAccess,
    VideoProgress,
    VideoPlaybackSession,
    VideoPlaybackEvent,
    VideoFolder,
)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "session", "order", "duration", "status")
    list_display_links = ("id", "title")
    list_filter = ("status", "session__lecture", "session")
    search_fields = ("title",)
    ordering = ("session", "order")


@admin.register(VideoAccess)
class VideoAccessAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "enrollment",
        "access_mode",
        "rule",
        "allow_skip_override",
        "max_speed_override",
        "show_watermark_override",
        "block_seek",
        "block_speed_control",
        "is_override",
        "proctored_completed_at",
    )
    list_display_links = ("id", "video")
    list_filter = ("access_mode", "rule", "video__session__lecture", "block_seek", "block_speed_control")
    search_fields = ("enrollment__student__name",)
    ordering = ("-id",)


@admin.register(VideoProgress)
class VideoProgressAdmin(admin.ModelAdmin):
    list_display = ("id", "video", "enrollment", "progress", "completed", "updated_at")
    list_display_links = ("id", "video")
    list_filter = ("video__session__lecture", "completed")
    search_fields = ("enrollment__student__name",)
    ordering = ("-updated_at",)


@admin.register(VideoPlaybackSession)
class VideoPlaybackSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "enrollment",
        "session_id",
        "device_id",
        "status",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "video__session__lecture", "video")
    search_fields = ("session_id", "device_id", "enrollment__student__name")
    ordering = ("-started_at",)


@admin.register(VideoPlaybackEvent)
class VideoPlaybackEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "enrollment",
        "session_id",
        "user_id",
        "event_type",
        "violated",
        "violation_reason",
        "occurred_at",
        "received_at",
    )
    list_filter = ("event_type", "violated", "video__session__lecture")
    search_fields = ("session_id", "enrollment__student__name", "user_id")
    ordering = ("-received_at",)


@admin.register(VideoFolder)
class VideoFolderAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "session", "parent", "order", "created_at")
    list_display_links = ("id", "name")
    list_filter = ("session__lecture", "session")
    search_fields = ("name",)
    ordering = ("session", "order", "name")
