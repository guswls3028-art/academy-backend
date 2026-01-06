# apps/domains/student_app/urls.py
from django.urls import path

from .dashboard.views import StudentDashboardView
from .sessions.views import StudentSessionListView, StudentSessionDetailView
from .exams.views import StudentExamListView, StudentExamDetailView
from .results.views import (
    MyExamResultView,
    MyExamResultItemsView,
)

urlpatterns = [
    # Dashboard
    path("student/dashboard/", StudentDashboardView.as_view()),

    # Sessions
    path("sessions/me/", StudentSessionListView.as_view()),
    path("sessions/<int:pk>/", StudentSessionDetailView.as_view()),

    # Exams
    path("exams/", StudentExamListView.as_view()),
    path("exams/<int:pk>/", StudentExamDetailView.as_view()),

    # Results
    path("results/me/exams/<int:exam_id>/", MyExamResultView.as_view()),
    path("results/me/exams/<int:exam_id>/items/", MyExamResultItemsView.as_view()),
]
