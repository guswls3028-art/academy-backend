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
        "video_required_rate",
        "exam_start_session_order",
        "exam_end_session_order",
        "exam_pass_score",
        "homework_start_session_order",
        "homework_end_session_order",
        "homework_pass_type",
        "created_at",
    )
    list_filter = ("homework_pass_type",)
    search_fields = ("lecture__title", "lecture__name")
    ordering = ("-id",)


@admin.register(SessionProgress)
class SessionProgressAdmin(admin.ModelAdmin):
    """
    ✅ SessionProgress Admin (집계 결과 전용)

    설계 원칙:
    - ❌ 시험 점수(exam_score)는 여기 책임이 아님
      → Result / SessionExamsSummary API에서만 조회
    - ✅ pass/fail 여부는 '집계 결과'이므로 유지
    - ✅ clinic 여부는 ClinicLink 도메인에서 별도 관리
    """

    list_display = (
        "id",
        "enrollment_id",
        "session",
        "attendance_type",
        "video_progress_rate",
        "video_completed",

        # ❌ REMOVED:
        # "exam_score",  # 시험 점수는 Result 도메인 책임

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
    """
    ✅ ClinicLink = 클리닉 트리거 단일 진실
    SessionProgress에서 분리된 구조 유지
    """
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
