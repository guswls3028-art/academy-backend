# PATH: apps/domains/results/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

# ======================================================
# Student
# ======================================================
from apps.domains.results.views.student_exam_result_view import MyExamResultView

# 🔧 PATCH: 학생 본인 Attempt 히스토리
from apps.domains.results.views.student_exam_attempts_view import (
    MyExamAttemptsView,
)

# ======================================================
# Admin / Teacher
# ======================================================
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView

# ⚠️ DEPRECATED (1:1 Session-Exam 가정)
# - 프론트 전환 완료 후 제거 예정
from apps.domains.results.views.admin_exam_summary_view import (
    AdminExamSummaryView,
)

from apps.domains.results.views.admin_representative_attempt_view import (
    AdminRepresentativeAttemptView,
)

# ✅ 단일 학생 결과 상세
from apps.domains.results.views.admin_exam_result_detail_view import (
    AdminExamResultDetailView,
)

# 🔧 PATCH: Session → Exam 목록 (1:N 시험 구조 대비)
from apps.domains.results.views.admin_session_exams_view import (
    AdminSessionExamsView,
)

# 🔧 PATCH: ResultFact 디버그 조회
from apps.domains.results.views.admin_result_fact_view import (
    AdminResultFactView,
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
# ExamAttempt (history / retake) - Admin only
# ======================================================
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet

# ======================================================
# Session score summary (Admin)
# ======================================================
from apps.domains.results.views.session_score_summary_view import (
    SessionScoreSummaryView,
)

# ======================================================
# Session 기준 시험 요약 API (1:N Exam 대응)
# ======================================================
from apps.domains.results.views.admin_session_exams_summary_view import (
    AdminSessionExamsSummaryView,
)

# ======================================================
# Admin 문항 점수 PATCH
# ======================================================
from apps.domains.results.views.admin_exam_item_score_view import (
    AdminExamItemScoreView,
)

# ======================================================
# 🔥 NEW: SessionScores API (성적 탭 메인 테이블)
# ======================================================
from apps.domains.results.views.session_scores_view import (
    SessionScoresView,
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

    # ----------------------------
    # Question stats
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
    # Representative attempt
    # ----------------------------
    path(
        "admin/exams/<int:exam_id>/representative-attempt/",
        AdminRepresentativeAttemptView.as_view(),
        name="admin-representative-attempt",
    ),

    # ============================
    # Session (Admin)
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

    # 🔥 핵심: 성적 탭 메인 테이블
    path(
        "admin/sessions/<int:session_id>/scores/",
        SessionScoresView.as_view(),
        name="session-scores",
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
# ExamAttempt router (Admin only)
# ================================
attempt_router = DefaultRouter()
attempt_router.register("exam-attempts", ExamAttemptViewSet)
urlpatterns += attempt_router.urls

# ======================================================
# ExamAttempt (Admin: per exam/enrollment)
# ======================================================
from apps.domains.results.views.admin_exam_attempts_view import (
    AdminExamAttemptsView,
)

urlpatterns += [
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/attempts/",
        AdminExamAttemptsView.as_view(),
        name="admin-exam-attempts",
    ),
]
