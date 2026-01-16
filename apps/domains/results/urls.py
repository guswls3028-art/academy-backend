# PATH: apps/domains/results/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

# ======================================================
# Student
# ======================================================
from apps.domains.results.views.student_exam_result_view import MyExamResultView
from apps.domains.results.views.student_exam_attempts_view import (
    MyExamAttemptsView,
)

# ======================================================
# Admin / Teacher
# ======================================================
from apps.domains.results.views.admin_exam_results_view import (
    AdminExamResultsView,
)

# ⚠️ DEPRECATED (1:1 Session-Exam 가정)
from apps.domains.results.views.admin_exam_summary_view import (
    AdminExamSummaryView,
)

from apps.domains.results.views.admin_exam_result_detail_view import (
    AdminExamResultDetailView,
)

from apps.domains.results.views.admin_representative_attempt_view import (
    AdminRepresentativeAttemptView,
)

# 🔧 PATCH: 문항 단위 수동 채점
from apps.domains.results.views.admin_exam_item_score_view import (
    AdminExamItemScoreView,
)

# ======================================================
# Session / Exam Meta
# ======================================================
from apps.domains.results.views.admin_session_exams_view import (
    AdminSessionExamsView,
)
from apps.domains.results.views.admin_session_exams_summary_view import (
    AdminSessionExamsSummaryView,
)
from apps.domains.results.views.session_score_summary_view import (
    SessionScoreSummaryView,
)

# ======================================================
# Question statistics
# ======================================================
from apps.domains.results.views.question_stats_views import (
    AdminExamQuestionStatsView,
    ExamQuestionWrongDistributionView,
    ExamTopWrongQuestionsView,
)

# ======================================================
# ResultFact (Debug)
# ======================================================
from apps.domains.results.views.admin_result_fact_view import (
    AdminResultFactView,
)

# ======================================================
# Wrong Note
# ======================================================
from apps.domains.results.views.wrong_note_view import WrongNoteView
from apps.domains.results.views.wrong_note_pdf_view import (
    WrongNotePDFCreateView,
)
from apps.domains.results.views.wrong_note_pdf_status_view import (
    WrongNotePDFStatusView,
)

# ======================================================
# ExamAttempt (Admin CRUD)
# ======================================================
from apps.domains.results.views.exam_attempt_view import (
    ExamAttemptViewSet,
)

# ======================================================
# ExamAttempt (Admin: per exam/enrollment 조회)
# ======================================================
from apps.domains.results.views.admin_exam_attempts_view import (
    AdminExamAttemptsView,
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
    path(
        "me/exams/<int:exam_id>/attempts/",
        MyExamAttemptsView.as_view(),
        name="my-exam-attempts",
    ),

    # ============================
    # Admin / Teacher
    # ============================

    # ⚠️ Legacy summary (제거 예정)
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

    # ✅ 단일 학생 결과 상세 (진실의 원천)
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/",
        AdminExamResultDetailView.as_view(),
        name="admin-exam-result-detail",
    ),

    # ============================
    # 🔥 문항 단위 수동 채점 (핵심)
    # ============================
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/items/<int:question_id>/",
        AdminExamItemScoreView.as_view(),
        name="admin-exam-item-score",
    ),

    # ============================
    # Question Statistics
    # ============================
    path(
        "admin/exams/<int:exam_id>/questions/",
        AdminExamQuestionStatsView.as_view(),
        name="admin-exam-question-stats",
    ),
    path(
        "admin/exams/<int:exam_id>/questions/<int:question_id>/wrong-distribution/",
        ExamQuestionWrongDistributionView.as_view(),
        name="admin-exam-question-wrong-distribution",
    ),
    path(
        "admin/exams/<int:exam_id>/questions/top-wrong/",
        ExamTopWrongQuestionsView.as_view(),
        name="admin-exam-top-wrong-questions",
    ),

    # ============================
    # Attempt
    # ============================
    path(
        "admin/exams/<int:exam_id>/representative-attempt/",
        AdminRepresentativeAttemptView.as_view(),
        name="admin-representative-attempt",
    ),
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/attempts/",
        AdminExamAttemptsView.as_view(),
        name="admin-exam-attempts",
    ),

    # ============================
    # Session
    # ============================
    path(
        "admin/sessions/<int:session_id>/score-summary/",
        SessionScoreSummaryView.as_view(),
        name="session-score-summary",
    ),
    path(
        "admin/sessions/<int:session_id>/exams/",
        AdminSessionExamsView.as_view(),
        name="admin-session-exams",
    ),
    path(
        "admin/sessions/<int:session_id>/exams/summary/",
        AdminSessionExamsSummaryView.as_view(),
        name="admin-session-exams-summary",
    ),

    # ============================
    # ResultFact (Debug)
    # ============================
    path(
        "admin/facts/",
        AdminResultFactView.as_view(),
        name="admin-result-facts",
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
# ExamAttempt CRUD (Admin only)
# ================================
attempt_router = DefaultRouter()
attempt_router.register("exam-attempts", ExamAttemptViewSet)
urlpatterns += attempt_router.urls
