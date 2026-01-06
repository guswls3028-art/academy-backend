# apps/domains/results/urls.py
"""
Results Domain API Routes

정책:
- Student / Admin API 명확히 분리
- Legacy API는 DEPRECATED 처리
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

# ======================
# Student
# ======================
from apps.domains.results.views.student_exam_result_view import MyExamResultView

# ======================
# Admin / Teacher (⭐ 권장)
# ======================
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView
from apps.domains.results.views.admin_exam_summary_view import AdminExamSummaryView
from apps.domains.results.views.admin_exam_question_stats_view import (
    AdminExamQuestionStatsView,
)

# ======================
# Wrong Notes
# ======================
from apps.domains.results.views.wrong_note_view import WrongNoteView

# ✅ PDF 생성 API (URL 등록 누락 버그 수정)
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView

# ======================
# Legacy
# ======================
from apps.domains.results.views.exam_result_view import (
    ExamStatsView,
    ExamQuestionStatsView,
)

# ======================
# Attempt
# ======================
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet


urlpatterns = [
    # -------------------
    # Student
    # -------------------
    path(
        "me/exams/<int:exam_id>/",
        MyExamResultView.as_view(),
        name="my-exam-result",
    ),

    # -------------------
    # Admin / Teacher
    # -------------------
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

    # -------------------
    # Wrong Notes
    # -------------------
    path(
        "wrong-notes",
        WrongNoteView.as_view(),
        name="wrong-note",
    ),

    # ✅ WrongNote PDF 생성 (기존 누락된 라우트)
    path(
        "wrong-notes/pdf/",
        WrongNotePDFCreateView.as_view(),
        name="wrong-note-pdf-create",
    ),

    # -------------------
    # ⚠️ Legacy (DEPRECATED)
    # -------------------
    path(
        "exams/<int:exam_id>/stats",
        ExamStatsView.as_view(),
        name="legacy-exam-stats",
    ),
    path(
        "exams/<int:exam_id>/questions/stats",
        ExamQuestionStatsView.as_view(),
        name="legacy-exam-question-stats",
    ),
]

# ======================
# Attempt Router
# ======================
attempt_router = DefaultRouter()
attempt_router.register("exam-attempts", ExamAttemptViewSet)

urlpatterns += attempt_router.urls
