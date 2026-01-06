# apps/domains/exams/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView

router = DefaultRouter()

# ===========================
# ✅ 핵심 수정 포인트
# ===========================
# v1 urls.py 에서 이미 "exams/" prefix를 붙이므로
# 여기서는 "" (root)에 등록해야 REST 표준이 됨
#
# 결과:
#   GET /api/v1/exams/
#   GET /api/v1/exams/{id}/
# ===========================
router.register("", ExamViewSet, basename="exam")

router.register("sheets", SheetViewSet)
router.register("questions", QuestionViewSet)
router.register("answer-keys", AnswerKeyViewSet)

urlpatterns = router.urls + [
    path(
        "sheets/<int:sheet_id>/auto-questions/",
        SheetAutoQuestionsView.as_view(),
    ),
]
