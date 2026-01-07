# PATH: apps/domains/results/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

# ======================================================
# Student
# ======================================================
from apps.domains.results.views.student_exam_result_view import MyExamResultView

# ======================================================
# Admin / Teacher
# ======================================================
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView
from apps.domains.results.views.admin_exam_summary_view import AdminExamSummaryView
from apps.domains.results.views.admin_representative_attempt_view import (
    AdminRepresentativeAttemptView,
)

# ✅ 추가: 단일 학생 결과 상세
from apps.domains.results.views.admin_exam_result_detail_view import (
    AdminExamResultDetailView,
)

# ======================================================
# Question statistics (STEP 2)
# ======================================================
from apps.domains.results.views.question_stats_views import (
    AdminExamQuestionStatsView,
    ExamQuestionWrongDistributionView,
    ExamTopWrongQuestionsView,
)

# ======================================================
# Wrong note
# ======================================================
from apps.domains.results.views.wrong_note_view import WrongNoteView
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView
from apps.domains.results.views.wrong_note_pdf_status_view import WrongNotePDFStatusView

# ======================================================
# ExamAttempt (history / retake)
# ======================================================
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet

# ======================================================
# Session score summary (Admin)
# ======================================================
# 🔧 PATCH: 세션 단위 성적 요약 API
from apps.domains.results.views.session_score_summary_view import (
    SessionScoreSummaryView,
)


urlpatterns = [
    # ============================
    # Student
    # ============================
    path(
        "me/exams/<int:exam_id>/",
        MyExamResultView.as_view(),
        name="my-exam-result",
    ),

    # ============================
    # Admin / Teacher
    # ============================
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

    # ✅ 단일 학생 결과 상세 (리스트 API와 분리)
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/",
        AdminExamResultDetailView.as_view(),
        name="admin-exam-result-detail",
    ),

    # ----------------------------
    # STEP 2-A: 문항 기본 통계
    # ----------------------------
    path(
        "admin/exams/<int:exam_id>/questions/",
        AdminExamQuestionStatsView.as_view(),
        name="admin-exam-question-stats",
    ),

    # ----------------------------
    # STEP 2-B: 단일 문항 오답 분포
    # ----------------------------
    path(
        "admin/exams/<int:exam_id>/questions/<int:question_id>/wrong-distribution/",
        ExamQuestionWrongDistributionView.as_view(),
        name="admin-exam-question-wrong-distribution",
    ),

    # ----------------------------
    # STEP 2-C: Top N 오답 문항
    # ----------------------------
    path(
        "admin/exams/<int:exam_id>/questions/top-wrong/",
        ExamTopWrongQuestionsView.as_view(),
        name="admin-exam-top-wrong-questions",
    ),

    # ============================
    # STEP 8-B: 대표 attempt 변경
    # ============================
    path(
        "admin/exams/<int:exam_id>/representative-attempt/",
        AdminRepresentativeAttemptView.as_view(),
        name="admin-representative-attempt",
    ),

    # ============================
    # Session Scores (Admin)
    # ============================
    # 🔧 PATCH: 세션 단위 최종 성적 요약
    path(
        "admin/sessions/<int:session_id>/score-summary/",
        SessionScoreSummaryView.as_view(),
        name="session-score-summary",
    ),

    # ============================
    # Wrong Notes
    # ============================
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
    path(
        "wrong-notes/pdf/<int:job_id>/",
        WrongNotePDFStatusView.as_view(),
        name="wrong-note-pdf-status",
    ),
]

# ================================
# ExamAttempt router
# ================================
attempt_router = DefaultRouter()
attempt_router.register("exam-attempts", ExamAttemptViewSet)
urlpatterns += attempt_router.urls
