# apps/domains/results/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.domains.results.views.student_exam_result_view import MyExamResultView
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView
from apps.domains.results.views.admin_exam_summary_view import AdminExamSummaryView
from apps.domains.results.views.admin_exam_question_stats_view import (
    AdminExamQuestionStatsView,
)
from apps.domains.results.views.wrong_note_view import WrongNoteView
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet

# ✅ STEP 8-B 추가
from apps.domains.results.views.admin_representative_attempt_view import (
    AdminRepresentativeAttemptView,
)

urlpatterns = [
    # Student
    path(
        "me/exams/<int:exam_id>/",
        MyExamResultView.as_view(),
        name="my-exam-result",
    ),

    # Admin / Teacher
    path(
        "admin/exams/<int:exam_id>/summary/",
        AdminExamSummaryView.as_view(),
        name="admin-exam-summary",
    ),
    path(
        "admin/exams/<int:exam_id>/results/",
        AdminExamResultsView.as_view(),
        name="admin-exam-results",
    ),
    path(
        "admin/exams/<int:exam_id>/questions/",
        AdminExamQuestionStatsView.as_view(),
        name="admin-exam-question-stats",
    ),

    # ==============================
    # ✅ STEP 8-B: 대표 attempt 변경
    # ==============================
    path(
        "admin/exams/<int:exam_id>/representative-attempt/",
        AdminRepresentativeAttemptView.as_view(),
        name="admin-representative-attempt",
    ),

    # Wrong Notes
    path(
        "wrong-notes",
        WrongNoteView.as_view(),
        name="wrong-note",
    ),
    path(
        "wrong-notes/pdf/",
        WrongNotePDFCreateView.as_view(),
        name="wrong-note-pdf-create",
    ),
]

attempt_router = DefaultRouter()
attempt_router.register("exam-attempts", ExamAttemptViewSet)
urlpatterns += attempt_router.urls
