# apps/api/v1/urls.py

from django.urls import path, include
from apps.support.media.views import VideoProcessingCompleteView

urlpatterns = [
    path("lectures/", include("apps.domains.lectures.urls")),
    path("lectures/", include("apps.domains.attendance.urls")),

    path("students/", include("apps.domains.students.urls")),
    path("enrollments/", include("apps.domains.enrollment.urls")),
    path("submissions/", include("apps.domains.submissions.urls")),
    path("exams/", include("apps.domains.exams.urls")),
    path("progress/", include("apps.domains.progress.urls")),
    path("results/", include("apps.domains.results.urls")),

    path("core/", include("apps.core.urls")),
    path("media/", include("apps.support.media.urls")),

    # ai
    path("internal/ai/", include("apps.api.v1.internal.ai.urls")),

    # 내부 워커 콜백
    path(
        "internal/videos/<int:video_id>/processing-complete/",
        VideoProcessingCompleteView.as_view(),
        name="video-processing-complete",
    ),

    #학생용앱 (사용자가 학생)
    path("api/v1/student/", include("apps.domains.student_app.urls")),
]
