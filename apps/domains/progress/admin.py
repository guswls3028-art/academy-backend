# apps/domains/progress/admin.py
from django.contrib import admin

from .models import (
    ProgressPolicy,
    SessionProgress,
    LectureProgress,
    ClinicLink,
    RiskLog,
)


@admin.register(ProgressPolicy)
class ProgressPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lecture",

        # ---------- video ----------
        "video_required_rate",

        # ---------- exam ----------
        "exam_start_session_order",
        "exam_end_session_order",
        "exam_pass_score",
        "exam_aggregate_strategy",
        "exam_pass_source",

        # ---------- homework ----------
        "homework_start_session_order",
        "homework_end_session_order",
        "homework_pass_type",

        # ✅ STEP 1: homework policy 표시
        "homework_cutline_percent",
        "homework_round_unit",

        "created_at",
    )

    list_filter = (
        "homework_pass_type",
        "exam_aggregate_strategy",
        "exam_pass_source",
    )

    search_fields = ("lecture__title", "lecture__name")
    ordering = ("-id",)


@admin.register(SessionProgress)
class SessionProgressAdmin(admin.ModelAdmin):
    """
    ✅ SessionProgress Admin (집계 결과 전용)
    """

    list_display = (
        "id",
        "enrollment_id",
        "session",
        "attendance_type",
        "video_progress_rate",
        "video_completed",

        "exam_passed",
        "homework_submitted",
        "homework_passed",

        "completed",
        "calculated_at",
        "updated_at",
    )

    list_filter = (
        "attendance_type",
        "completed",
        "exam_passed",
        "homework_passed",
    )

    search_fields = (
        "enrollment_id",
        "session__title",
        "session__lecture__title",
    )

    ordering = ("-updated_at", "-id")


@admin.register(LectureProgress)
class LectureProgressAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enrollment_id",
        "lecture",
        "total_sessions",
        "completed_sessions",
        "failed_sessions",
        "consecutive_failed_sessions",
        "risk_level",
        "last_session",
        "last_updated",
        "updated_at",
    )
    list_filter = ("risk_level", "lecture")
    search_fields = ("enrollment_id", "lecture__title", "lecture__name")
    ordering = ("-updated_at", "-id")


@admin.register(ClinicLink)
class ClinicLinkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enrollment_id",
        "session",
        "reason",
        "is_auto",
        "approved",
        "created_at",
    )
    list_filter = ("reason", "is_auto", "approved")
    search_fields = ("enrollment_id", "session__title", "session__lecture__title")
    ordering = ("-created_at", "-id")


@admin.register(RiskLog)
class RiskLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enrollment_id",
        "session",
        "risk_level",
        "rule",
        "created_at",
    )
    list_filter = ("risk_level", "rule")
    search_fields = ("enrollment_id",)
    ordering = ("-created_at", "-id")
