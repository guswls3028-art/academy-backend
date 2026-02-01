# apps/domains/results/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.domains.results.views import (
    MyExamResultView,
    WrongNoteView,
    AdminExamResultsView,
    AdminExamSummaryView,
)

from apps.domains.results.views.admin_exam_attempts_view import AdminExamAttemptsView
from apps.domains.results.views.admin_exam_item_score_view import AdminExamItemScoreView
from apps.domains.results.views.admin_exam_result_detail_view import AdminExamResultDetailView
from apps.domains.results.views.admin_representative_attempt_view import AdminRepresentativeAttemptView
from apps.domains.results.views.admin_result_fact_view import AdminResultFactView
from apps.domains.results.views.admin_session_exams_view import AdminSessionExamsView
from apps.domains.results.views.admin_session_exams_summary_view import AdminSessionExamsSummaryView
from apps.domains.results.views.question_stats_views import (
    AdminExamQuestionStatsView,
    ExamQuestionWrongDistributionView,
    ExamTopWrongQuestionsView,
)
from apps.domains.results.views.session_score_summary_view import SessionScoreSummaryView
from apps.domains.results.views.session_scores_view import SessionScoresView
from apps.domains.results.views.student_exam_attempts_view import MyExamAttemptsView
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView
from apps.domains.results.views.wrong_note_pdf_status_view import WrongNotePDFStatusView
from apps.domains.results.views.exam_grading_view import (
    AutoGradeSubmissionView,
    ManualGradeSubmissionView,
    FinalizeResultView,
    ExamResultAdminListView,
    MyExamResultListView,
)

router = DefaultRouter()
router.register(r"", ExamResultAdminListView, basename="exam-results")

urlpatterns = [
    # =========================
    # Student
    # =========================
    path("me/exams/<int:exam_id>/", MyExamResultView.as_view(), name="my-exam-result"),
    path("me/exams/<int:exam_id>/attempts/", MyExamAttemptsView.as_view(), name="my-exam-attempts"),
    path("me/", MyExamResultListView.as_view(), name="my-exam-results"),

    # =========================
    # Admin / Teacher – Exam
    # =========================
    path("admin/exams/<int:exam_id>/results/", AdminExamResultsView.as_view(), name="admin-exam-results"),
    path("admin/exams/<int:exam_id>/summary/", AdminExamSummaryView.as_view(), name="admin-exam-summary"),
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/",
        AdminExamResultDetailView.as_view(),
        name="admin-exam-result-detail",
    ),
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/attempts/",
        AdminExamAttemptsView.as_view(),
        name="admin-exam-attempts",
    ),
    path(
        "admin/exams/<int:exam_id>/items/<int:question_id>/enrollments/<int:enrollment_id>/",
        AdminExamItemScoreView.as_view(),
        name="admin-exam-item-score",
    ),
    path(
        "admin/exams/<int:exam_id>/representative-attempt/",
        AdminRepresentativeAttemptView.as_view(),
        name="admin-representative-attempt",
    ),
    path("admin/facts/", AdminResultFactView.as_view(), name="admin-result-facts"),

    # =========================
    # Admin – Stats
    # =========================
    path(
        "admin/exams/<int:exam_id>/questions/",
        AdminExamQuestionStatsView.as_view(),
        name="admin-exam-question-stats",
    ),
    path(
        "admin/exams/<int:exam_id>/questions/<int:question_id>/wrong-distribution/",
        ExamQuestionWrongDistributionView.as_view(),
        name="exam-question-wrong-distribution",
    ),
    path(
        "admin/exams/<int:exam_id>/questions/top-wrong/",
        ExamTopWrongQuestionsView.as_view(),
        name="exam-top-wrong-questions",
    ),

    # =========================
    # Session
    # =========================
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
    path(
        "admin/sessions/<int:session_id>/score-summary/",
        SessionScoreSummaryView.as_view(),
        name="session-score-summary",
    ),
    path(
        "admin/sessions/<int:session_id>/scores/",
        SessionScoresView.as_view(),
        name="session-scores",
    ),

    # =========================
    # Grading (7-4~7-6)
    # =========================
    path(
        "submissions/<int:submission_id>/auto-grade/",
        AutoGradeSubmissionView.as_view(),
        name="auto-grade-submission",
    ),
    path(
        "submissions/<int:submission_id>/manual-grade/",
        ManualGradeSubmissionView.as_view(),
        name="manual-grade-submission",
    ),
    path(
        "submissions/<int:submission_id>/finalize/",
        FinalizeResultView.as_view(),
        name="finalize-submission",
    ),

    # =========================
    # Wrong Note
    # =========================
    path("wrong-notes/", WrongNoteView.as_view(), name="wrong-notes"),
    path("wrong-notes/pdf/", WrongNotePDFCreateView.as_view(), name="wrong-note-pdf-create"),
    path(
        "wrong-notes/pdf/<int:job_id>/",
        WrongNotePDFStatusView.as_view(),
        name="wrong-note-pdf-status",
    ),

    # Router
    path("", include(router.urls)),
]
