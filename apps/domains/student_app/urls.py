# PATH: C:\academy\apps\domains\student_app\urls.py
from django.urls import path

from .dashboard.views import StudentDashboardView
from .sessions.views import StudentSessionListView, StudentSessionDetailView
from .exams.views import (
    StudentExamListView,
    StudentExamDetailView,
    StudentExamQuestionsView,
    StudentExamSubmitView,
)
from .results.views import (
    MyExamResultView,
    MyExamResultItemsView,
)
from .profile.views import StudentProfileView

# ✅ NEW
from .media.views import (
    StudentSessionVideoListView,
    StudentVideoPlaybackView,
    StudentVideoProgressView,
    StudentPublicSessionView,
    StudentVideoMeView,
)

urlpatterns = [
    # 내 프로필 (학생 전용) — GET/PATCH
    path("me/", StudentProfileView.as_view()),

    # Dashboard
    path("dashboard/", StudentDashboardView.as_view()),

    # Sessions
    path("sessions/me/", StudentSessionListView.as_view()),
    path("sessions/<int:pk>/", StudentSessionDetailView.as_view()),

    # Exams
    path("exams/", StudentExamListView.as_view()),
    path("exams/<int:pk>/", StudentExamDetailView.as_view()),
    path("exams/<int:pk>/questions/", StudentExamQuestionsView.as_view()),
    path("exams/<int:pk>/submit/", StudentExamSubmitView.as_view()),

    # Results
    path("results/me/exams/<int:exam_id>/", MyExamResultView.as_view()),
    path("results/me/exams/<int:exam_id>/items/", MyExamResultItemsView.as_view()),

    # ✅ Video (Student Consumer)
    path("video/me/", StudentVideoMeView.as_view()),
    path("video/public-session/", StudentPublicSessionView.as_view()),
    path("video/sessions/<int:session_id>/videos/", StudentSessionVideoListView.as_view()),
    path("video/videos/<int:video_id>/playback/", StudentVideoPlaybackView.as_view()),
    path("video/videos/<int:video_id>/progress/", StudentVideoProgressView.as_view()),
]
