# apps/api/v1/urls.py
from django.urls import path, include
from apps.support.media.views import VideoProcessingCompleteView

urlpatterns = [
    # =========================
    # Domain APIs
    # =========================
    path("lectures/", include("apps.domains.lectures.urls")),

    # 🔥 출결은 lectures 하위로 이동
    path("lectures/", include("apps.domains.attendance.urls")),

    path("students/", include("apps.domains.students.urls")),
    path("enrollments/", include("apps.domains.enrollment.urls")),
    path("submissions/", include("apps.domains.submissions.urls")),
    path("exams/", include("apps.domains.exams.urls")),

    path("core/", include("apps.core.urls")),
    path("media/", include("apps.support.media.urls")),
    
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
