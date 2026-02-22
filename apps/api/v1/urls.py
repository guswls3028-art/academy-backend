# PATH: apps/api/v1/urls.py
# ⚠️ 기존 코드 유지 + support.video 전환 + internal video-worker include

from django.urls import path, include
from apps.support.video.views.internal_views import (
    VideoProcessingCompleteView,
    VideoBacklogCountView,
    VideoBacklogScoreView,
    VideoAsgInterruptStatusView,
    VideoDlqMarkDeadView,
    VideoDeleteR2InternalView,
    VideoScanStuckView,
)

urlpatterns = [
    # =========================
    # Lectures / Attendance
    # =========================
    path("lectures/", include("apps.domains.lectures.urls")),
    path("lectures/", include("apps.domains.attendance.urls")),

    # =========================
    # Core Domains
    # =========================
    path("students/", include("apps.domains.students.urls")),
    path("enrollments/", include("apps.domains.enrollment.urls")),
    path("submissions/", include("apps.domains.submissions.urls")),
    path("exams/", include("apps.domains.exams.urls")),
    path("progress/", include("apps.domains.progress.urls")),

    # =========================
    # Staff / Teacher
    # =========================
    path("staffs/", include("apps.domains.staffs.urls")),
    path("teachers/", include("apps.domains.teachers.urls")),

    # =========================
    # Results / Homework
    # =========================
    path("results/", include("apps.domains.results.urls")),
    path("homework/", include("apps.domains.homework.urls")),
    path("homeworks/", include("apps.domains.homework_results.urls")),

    # =========================
    # Clinic Domain
    # =========================
    path("clinic/", include("apps.domains.clinic.urls")),

    # =========================
    # ✅ Assets Domain
    # =========================
    path("assets/", include("apps.domains.assets.urls")),

    # =========================
    # Storage (인벤토리)
    # =========================
    path("storage/", include("apps.domains.inventory.urls")),

    # =========================
    # Community (SSOT)
    # =========================
    path("community/", include("apps.domains.community.api.urls")),

    # =========================
    # Messaging (알림톡 잔액/충전/연동/로그)
    # =========================
    path("messaging/", include("apps.support.messaging.urls")),

    # =========================
    # Core
    # =========================
    path("core/", include("apps.core.urls")),

    # =========================
    # ✅ Video Domain (media → video 전환)
    # - URL prefix는 기존 "media/" 유지 (외부 클라이언트 안정성)
    # - include 경로만 video로 전환
    # =========================
    path("media/", include("apps.support.video.urls")),

    # =========================
    # AI job 상태 조회 (엑셀 내보내기 등)
    # =========================
    path("jobs/", include("apps.domains.ai.urls")),

    # =========================
    # AI (internal)
    # =========================
    path("internal/ai/", include("apps.api.v1.internal.ai.urls")),

    # =========================
    # ✅ Video Worker (internal)
    # /api/v1/internal/video-worker/*
    # =========================
    path("internal/", include("apps.support.video.urls_internal")),

    # =========================
    # B1: Video BacklogCount (queue_depth_lambda → CloudWatch)
    # =========================
    path(
        "internal/video/backlog-count/",
        VideoBacklogCountView.as_view(),
        name="video-backlog-count",
    ),
    path(
        "internal/video/backlog/",
        VideoBacklogCountView.as_view(),
        name="video-backlog",
    ),
    path(
        "internal/video/backlog-score/",
        VideoBacklogScoreView.as_view(),
        name="video-backlog-score",
    ),
    path(
        "internal/video/asg-interrupt-status/",
        VideoAsgInterruptStatusView.as_view(),
        name="video-asg-interrupt-status",
    ),
    path(
        "internal/video/dlq-mark-dead/",
        VideoDlqMarkDeadView.as_view(),
        name="video-dlq-mark-dead",
    ),
    path(
        "internal/video/scan-stuck/",
        VideoScanStuckView.as_view(),
        name="video-scan-stuck",
    ),

    # =========================
    # Internal (Legacy ACK - kept)
    # =========================
    path(
        "internal/videos/<int:video_id>/processing-complete/",
        VideoProcessingCompleteView.as_view(),
        name="video-processing-complete",
    ),

    # =========================
    # Student App
    # =========================
    path("student/", include("apps.domains.student_app.urls")),
]
