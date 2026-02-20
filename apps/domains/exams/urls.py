# apps/domains/exams/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.domains.exams.views.exam_view import ExamViewSet
from apps.domains.exams.views.sheet_view import SheetViewSet
from apps.domains.exams.views.question_view import QuestionViewSet
from apps.domains.exams.views.answer_key_view import AnswerKeyViewSet

from apps.domains.exams.views.exam_asset_view import ExamAssetView
from apps.domains.exams.views.omr_generate_view import GenerateOMRSheetAssetView

from apps.domains.exams.views.template_builder_view import TemplateBuilderView
from apps.domains.exams.views.template_editor_view import TemplateEditorView
from apps.domains.exams.views.template_status_view import TemplateStatusView
from apps.domains.exams.views.template_validation_view import TemplateValidationView

from apps.domains.exams.views.regular_from_template_view import RegularExamFromTemplateView
from apps.domains.exams.views.exam_questions_by_exam_view import ExamQuestionsByExamView
from apps.domains.exams.views.question_auto_view import SheetAutoQuestionsView
from apps.domains.exams.views.exam_enrollment_view import ExamEnrollmentManageView
from apps.domains.exams.views.student_exam_view import StudentAvailableExamListView
from apps.domains.exams.views.bulk_template_create_view import BulkTemplateCreateView

router = DefaultRouter()
router.register(r"", ExamViewSet, basename="exams")
router.register(r"sheets", SheetViewSet, basename="exam-sheets")
router.register(r"questions", QuestionViewSet, basename="exam-questions")
router.register(r"answer-keys", AnswerKeyViewSet, basename="answer-keys")

urlpatterns = [
    # =========================
    # Bulk template (원테이크) — /exams/bulk-template/ 먼저 매칭
    # =========================
    path("bulk-template/", BulkTemplateCreateView.as_view()),

    # =========================
    # Core
    # =========================
    path("", include(router.urls)),

    # =========================
    # Template lifecycle
    # =========================
    path("<int:exam_id>/builder/", TemplateBuilderView.as_view()),
    path("<int:exam_id>/template-editor/", TemplateEditorView.as_view()),
    path("<int:exam_id>/template-status/", TemplateStatusView.as_view()),
    path("<int:exam_id>/template-validation/", TemplateValidationView.as_view()),

    # =========================
    # Regular exam creation
    # =========================
    path("<int:exam_id>/spawn-regular/", RegularExamFromTemplateView.as_view()),

    # =========================
    # Assets / OMR
    # =========================
    path("<int:exam_id>/assets/", ExamAssetView.as_view()),
    path("<int:exam_id>/generate-omr/", GenerateOMRSheetAssetView.as_view()),

    # =========================
    # Questions
    # =========================
    path("<int:exam_id>/questions/", ExamQuestionsByExamView.as_view()),
    path("sheets/<int:sheet_id>/auto-questions/", SheetAutoQuestionsView.as_view()),

    # =========================
    # Enrollment
    # =========================
    path("<int:exam_id>/enrollments/", ExamEnrollmentManageView.as_view()),

    # =========================
    # Student
    # =========================
    path("me/available/", StudentAvailableExamListView.as_view()),
]
