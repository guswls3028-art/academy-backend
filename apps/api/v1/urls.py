# apps/api/v1/urls.py
from django.urls import path, include
from apps.support.media.views import VideoProcessingCompleteView

urlpatterns = [
    # =========================
    # Domain APIs
    # =========================
    path("lectures/", include("apps.domains.lectures.urls")),
    path("students/", include("apps.domains.students.urls")),
    path("enrollments/", include("apps.domains.enrollment.urls")),
    path("attendances/", include("apps.domains.attendance.urls")),
    path("submissions/", include("apps.domains.submissions.urls")),
    path("exams/", include("apps.domains.exams.urls")),

    # =========================
    # Core (🔥 추가)
    # =========================
    path("core/", include("apps.core.urls")),

    # =========================
    # Media
    # =========================
    path("media/", include("apps.support.media.urls")),

    # ai가 추가하래서함 여기는 api\v1\urls.py
    path(
        "internal/videos/<int:video_id>/processing-complete/",
        VideoProcessingCompleteView.as_view(),
        name="video-processing-complete",
    ),


]
