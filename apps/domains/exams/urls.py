# PATH: apps/domains/exams/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet
from .views.question_auto_view import SheetAutoQuestionsView
from .views.exam_asset_view import ExamAssetView
from .views.exam_questions_by_exam_view import ExamQuestionsByExamView
from .views.exam_enrollment_view import ExamEnrollmentManageView
from .views.student_exam_view import StudentAvailableExamListView

router = DefaultRouter()

# NOTE: prefix가 이미 /exams/ 로 붙는 구조
router.register(r"", ExamViewSet, basename="exam")
router.register(r"sheets", SheetViewSet)
router.register(r"questions", QuestionViewSet)
router.register(r"answer-keys", AnswerKeyViewSet)

urlpatterns = [
    path("", include(router.urls)),

    # Sheet auto-question
    path(
        "sheets/<int:sheet_id>/auto-questions/",
        SheetAutoQuestionsView.as_view(),
        name="sheet-auto-questions",
    ),

    # Exam assets
    path(
        "<int:exam_id>/assets/",
        ExamAssetView.as_view(),
        name="exam-assets",
    ),

    # exam 기준 문항 조회
    path(
        "<int:exam_id>/questions/",
        ExamQuestionsByExamView.as_view(),
        name="exam-questions-by-exam",
    ),

    # 시험 대상자 관리
    path(
        "<int:exam_id>/enrollments/",
        ExamEnrollmentManageView.as_view(),
        name="exam-enrollments-manage",
    ),

    # 학생 기준 접근 가능한 시험 목록
    path(
        "me/available/",
        StudentAvailableExamListView.as_view(),
        name="student-available-exams",
    ),
]
