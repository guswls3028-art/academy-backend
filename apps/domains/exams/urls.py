# PATH: apps/domains/exams/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.exam_view import ExamViewSet
from .views.sheet_view import SheetViewSet
from .views.question_view import QuestionViewSet
from .views.answer_key_view import AnswerKeyViewSet

from .views.exam_asset_view import ExamAssetView
from .views.exam_questions_by_exam_view import ExamQuestionsByExamView
from .views.template_builder_view import TemplateBuilderView
from .views.template_editor_view import TemplateEditorView
from .views.template_status_view import TemplateStatusView
from .views.template_validation_view import TemplateValidationView
from .views.regular_from_template_view import RegularExamFromTemplateView
from .views.omr_generate_view import GenerateOMRSheetAssetView
from .views.exam_enrollment_view import ExamEnrollmentManageView
from .views.student_exam_view import StudentAvailableExamListView
from .views.question_auto_view import SheetAutoQuestionsView

router = DefaultRouter()
router.register(r"", ExamViewSet, basename="exams")
router.register(r"sheets", SheetViewSet, basename="sheets")
router.register(r"questions", QuestionViewSet, basename="questions")
router.register(r"answer-keys", AnswerKeyViewSet, basename="answer-keys")

urlpatterns = [
    # =========================
    # Student
    # =========================
    path("me/available/", StudentAvailableExamListView.as_view(), name="student-available-exams"),

    # =========================
    # Exam Assets / Questions (resolve template)
    # =========================
    path("<int:exam_id>/assets/", ExamAssetView.as_view(), name="exam-assets"),
    path("<int:exam_id>/questions/", ExamQuestionsByExamView.as_view(), name="exam-questions-by-exam"),

    # =========================
    # Template utilities
    # =========================
    path("<int:exam_id>/builder/", TemplateBuilderView.as_view(), name="template-builder"),
    path("<int:exam_id>/template-editor/", TemplateEditorView.as_view(), name="template-editor"),
    path("<int:exam_id>/template-status/", TemplateStatusView.as_view(), name="template-status"),
    path("<int:exam_id>/template-validation/", TemplateValidationView.as_view(), name="template-validation"),

    # =========================
    # Template -> Regular spawn
    # =========================
    path("<int:exam_id>/spawn-regular/", RegularExamFromTemplateView.as_view(), name="spawn-regular-from-template"),

    # =========================
    # OMR sheet generate (template only)
    # =========================
    path("<int:exam_id>/generate-omr/", GenerateOMRSheetAssetView.as_view(), name="generate-omr-sheet"),

    # =========================
    # Exam Enrollment manage
    # =========================
    path("<int:exam_id>/enrollments/", ExamEnrollmentManageView.as_view(), name="exam-enrollments"),

    # =========================
    # Sheet auto questions (segmentation result)
    # =========================
    path("sheets/<int:sheet_id>/auto-questions/", SheetAutoQuestionsView.as_view(), name="sheet-auto-questions"),

    # Router
    path("", include(router.urls)),
]
