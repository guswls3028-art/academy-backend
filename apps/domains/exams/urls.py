# apps/domains/exams/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView

router = DefaultRouter()

# ===========================
# exams prefix는 v1에서 이미 붙음
# ===========================
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
]
