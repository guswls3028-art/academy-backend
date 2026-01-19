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
# 🔧 PATCH: 세션 단위 "최종 성적" 요약 (Progress 기반)
from apps.domains.results.views.session_score_summary_view import (
    SessionScoreSummaryView,
)

# ======================================================
# ✅ NEW: Session 기준 시험 요약 API (1:N Exam 대응)
# ======================================================
from apps.domains.results.views.admin_session_exams_summary_view import (
    AdminSessionExamsSummaryView,
)

# ======================================================
# ✅ NEW: Admin 문항 점수 PATCH (라우팅 필수)
# ======================================================
from apps.domains.results.views.admin_exam_item_score_view import (
    AdminExamItemScoreView,
)

# ======================================================
# ✅ NEW: Clinic Targets (Admin/Teacher)
# ======================================================
from apps.domains.results.views.admin_clinic_targets_view import (
    AdminClinicTargetsView,
)

# ======================================================
# ✅ NEW: Session Scores (성적 탭 메인 테이블)
# ======================================================
# 🔥 핵심: results + homework + clinic 조합 API
from apps.domains.results.views.session_scores_view import SessionScoresView


urlpatterns = [
    # ============================
    # Student
    # ============================
    path(
        "me/exams/<int:exam_id>/",
        MyExamResultView.as_view(),
        name="my-exam-result",
    ),

    # 🔧 PATCH: 학생 본인 재시험 / Attempt 히스토리
    path(
        "me/exams/<int:exam_id>/attempts/",
        MyExamAttemptsView.as_view(),
        name="my-exam-attempts",
    ),

    # ============================
    # Admin / Teacher
    # ============================

    # ⚠️ DEPRECATED
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

    # ✅ 단일 학생 결과 상세
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/",
        AdminExamResultDetailView.as_view(),
        name="admin-exam-result-detail",
    ),

    # ✅ 문항 점수 수동 수정 (Phase 3)
    path(
        "admin/exams/<int:exam_id>/enrollments/<int:enrollment_id>/items/<int:question_id>/",
        AdminExamItemScoreView.as_view(),
        name="admin-exam-item-score",
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

    # 🔹 Progress 기반 세션 최종 성적 요약
    path(
        "admin/sessions/<int:session_id>/score-summary/",
        SessionScoreSummaryView.as_view(),
        name="session-score-summary",
    ),

    # 🔹 🔥 성적 탭 메인 테이블 (exam + homework + clinic)
    path(
        "admin/sessions/<int:session_id>/scores/",
        SessionScoresView.as_view(),
        name="admin-session-scores",
    ),

    # 🔹 Session → Exam 목록 (메타)
    path(
        "admin/sessions/<int:session_id>/exams/",
        AdminSessionExamsView.as_view(),
        name="admin-session-exams",
    ),

    # 🔥 Session 기준 시험 요약 (1:N Exam)
    path(
        "admin/sessions/<int:session_id>/exams/summary/",
        AdminSessionExamsSummaryView.as_view(),
        name="admin-session-exams-summary",
    ),

    # ============================
    # ResultFact (Debug / Admin)
    # ============================
    path(
        "admin/facts/",
        AdminResultFactView.as_view(),
        name="admin-result-facts",
    ),

    # ============================
    # ✅ Clinic Targets (Admin/Teacher)
    # ============================
    path(
        "admin/clinic-targets/",
        AdminClinicTargetsView.as_view(),
        name="admin-clinic-targets",
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
