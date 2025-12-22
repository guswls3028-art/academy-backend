# apps/domains/exams/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView

router = DefaultRouter()
router.register("exams", ExamViewSet)
router.register("sheets", SheetViewSet)
router.register("questions", QuestionViewSet)
router.register("answer-keys", AnswerKeyViewSet)

urlpatterns = router.urls + [
    path("sheets/<int:sheet_id>/auto-questions/", SheetAutoQuestionsView.as_view()),
]
