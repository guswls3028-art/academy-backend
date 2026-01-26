# PATH: apps/api/v1/urls.py

from django.urls import path, include
from apps.support.media.views import VideoProcessingCompleteView

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
    # Results / Homework
    # =========================
    path("results/", include("apps.domains.results.urls")),
    path("homework/", include("apps.domains.homework.urls")),
    path("homeworks/", include("apps.domains.homework_results.urls")),

    # =========================
    # ✅ Interactions (🔥 이게 핵심)
    # =========================
    path(
        "interactions/",
        include("apps.domains.interactions.urls"),
    ),

    # =========================
    # Core / Media
    # =========================
    path("core/", include("apps.core.urls")),
    path("media/", include("apps.support.media.urls")),

    # =========================
    # AI (internal)
    # =========================
    path("internal/ai/", include("apps.api.v1.internal.ai.urls")),

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
