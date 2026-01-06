# apps/domains/exams/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView
from .views.exam_asset_view import ExamAssetView

# ✅ STEP 8-A 추가
from .views.exam_questions_by_exam_view import ExamQuestionsByExamView

router = DefaultRouter()

router.register(r"", ExamViewSet, basename="exam")
router.register(r"sheets", SheetViewSet)
router.register(r"questions", QuestionViewSet)
router.register(r"answer-keys", AnswerKeyViewSet)

urlpatterns = [
    path("", include(router.urls)),

    path(
        "sheets/<int:sheet_id>/auto-questions/",
        SheetAutoQuestionsView.as_view(),
        name="sheet-auto-questions",
    ),

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
]
