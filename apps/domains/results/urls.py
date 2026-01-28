# PATH: apps/domains/results/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

# ======================================================
# Student Views
# ======================================================
from apps.domains.results.views.student_exam_result_view import MyExamResultView
from apps.domains.results.views.student_exam_attempts_view import (
    MyExamAttemptsView,
)

# ======================================================
# Admin / Teacher - Exam
# ======================================================
from apps.domains.results.views.admin_exam_results_view import (
    AdminExamResultsView,
)
from apps.domains.results.views.admin_exam_summary_view import (
    AdminExamSummaryView,
)
from apps.domains.results.views.admin_exam_result_detail_view import (
    AdminExamResultDetailView,
)
from apps.domains.results.views.admin_exam_item_score_view import (
    AdminExamItemScoreView,
)
from apps.domains.results.views.admin_representative_attempt_view import (
    AdminRepresentativeAttemptView,
)
from apps.domains.results.views.admin_exam_attempts_view import (
    AdminExamAttemptsView,
)

# ======================================================
# Admin / Teacher - Session
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
from apps.domains.results.views.session_scores_view import (
    SessionScoresView,
)

# ======================================================
# Admin / Teacher - Result / Fact
# ======================================================
from apps.domains.results.views.admin_result_fact_view import (
    AdminResultFactView,
)

# ======================================================
# Question Statistics
# ======================================================
from apps.domains.results.views.question_stats_views import (
    AdminExamQuestionStatsView,
    ExamQuestionWrongDistributionView,
    ExamTopWrongQuestionsView,
)

# ======================================================
# Wrong Note
# ======================================================
from apps.domains.results.views.wrong_note_view import (
    WrongNoteView,
)
from apps.domains.results.views.wrong_note_pdf_view import (
    WrongNotePDFCreateView,
)
from apps.domains.results.views.wrong_note_pdf_status_view import (
    WrongNotePDFStatusView,
)

# ======================================================
# ExamAttempt (ViewSet)
# ======================================================
from apps.domains.results.views.exam_attempt_view import (
    ExamAttemptViewSet,
)

# ======================================================
# Clinic Targets (results domain)
# ======================================================
from apps.domains.results.views.admin_clinic_targets_view import (
    AdminClinicTargetsView,
)

# ======================================================
# Clinic Bookings (SAFE ALIAS)
# ------------------------------------------------------
# results 도메인에 View를 새로 만들지 않고,
# clinic 도메인의 ParticipantViewSet을 그대로 사용한다.
# - 단일 Source of Truth 유지
# - 중복 로직 방지
# - URL 구조는 results 기준 유지
# ======================================================
from apps.domains.clinic.views import (
    ParticipantViewSet as AdminClinicBookingViewSet,
)

# ======================================================
# URL Patterns
# ======================================================
urlpatterns = [
    # ----------------------------
    # Student
    # ----------------------------
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

    # ----------------------------
    # Admin / Teacher - Exam
    # ----------------------------
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
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/",
        AdminExamResultDetailView.as_view(),
        name="admin-exam-result-detail",
    ),
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/items/<int:question_id>/",
        AdminExamItemScoreView.as_view(),
        name="admin-exam-item-score",
    ),
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

    # ----------------------------
    # Admin / Teacher - Question Stats
    # ----------------------------
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

    # ----------------------------
    # Admin / Teacher - Session
    # ----------------------------
    path(
        "admin/sessions/<int:session_id>/score-summary/",
        SessionScoreSummaryView.as_view(),
        name="session-score-summary",
    ),
    path(
        "admin/sessions/<int:session_id>/scores/",
        SessionScoresView.as_view(),
        name="admin-session-scores",
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

    # ----------------------------
    # Admin / Teacher - Result Fact
    # ----------------------------
    path(
        "admin/facts/",
        AdminResultFactView.as_view(),
        name="admin-result-facts",
    ),

    # ----------------------------
    # Admin / Teacher - Clinic
    # ----------------------------
    path(
        "admin/clinic-targets/",
        AdminClinicTargetsView.as_view(),
        name="admin-clinic-targets",
    ),

    # ----------------------------
    # Wrong Notes
    # ----------------------------
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

# ======================================================
# Routers
# ======================================================
router = DefaultRouter()
router.register(
    "exam-attempts",
    ExamAttemptViewSet,
)
router.register(
    "admin/clinic-bookings",
    AdminClinicBookingViewSet,
    basename="admin-clinic-bookings",
)

urlpatterns += router.urls
