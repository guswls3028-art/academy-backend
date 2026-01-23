# PATH: apps/domains/exams/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView
from .views.exam_asset_view import ExamAssetView

# ✅ STEP 8-A: exam 기준 문항 조회
from .views.exam_questions_by_exam_view import ExamQuestionsByExamView

# ✅ NEW: 시험 대상자 관리
from .views.exam_enrollment_view import ExamEnrollmentManageView

router = DefaultRouter()

# NOTE:
# 이 exams 도메인은 prefix가 이미 /exams/ 로 붙는 구조라서
# 여기 router.register(r"", ExamViewSet) 형태 유지
router.register(r"", ExamViewSet, basename="exam")
router.register(r"sheets", SheetViewSet)
router.register(r"questions", QuestionViewSet)
router.register(r"answer-keys", AnswerKeyViewSet)

urlpatterns = [
    path("", include(router.urls)),

    # ==============================
    # Sheet auto-question
    # ==============================
    path(
        "sheets/<int:sheet_id>/auto-questions/",
        SheetAutoQuestionsView.as_view(),
        name="sheet-auto-questions",
    ),

    # ==============================
    # Exam assets
    # ==============================
    path(
        "<int:exam_id>/assets/",
        ExamAssetView.as_view(),
        name="exam-assets",
    ),

    # ==============================
    # ✅ STEP 8-A: exam 기준 문항 조회
    # ==============================
    path(
        "<int:exam_id>/questions/",
        ExamQuestionsByExamView.as_view(),
        name="exam-questions-by-exam",
    ),

    # ==============================
    # ✅ NEW: 시험 대상자 관리
    # ==============================
    path(
        "<int:exam_id>/enrollments/",
        ExamEnrollmentManageView.as_view(),
        name="exam-enrollments-manage",
    ),
]
