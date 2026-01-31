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

from .views.template_builder_view import TemplateBuilderView
from .views.template_status_view import TemplateStatusView
from .views.template_editor_view import TemplateEditorView
from .views.template_validation_view import TemplateValidationView

# ✅ PHASE 2 신규(아래에서 제공)
from .views.regular_from_template_view import RegularExamFromTemplateView
from .views.omr_generate_view import GenerateOMRSheetAssetView

router = DefaultRouter()
router.register(r"", ExamViewSet, basename="exam")
router.register(r"sheets", SheetViewSet)
router.register(r"questions", QuestionViewSet)
router.register(r"answer-keys", AnswerKeyViewSet)

urlpatterns = [
    # =========================
    # ✅ 기존 exams API (유지)
    # =========================
    path("", include(router.urls)),
    path("sheets/<int:sheet_id>/auto-questions/", SheetAutoQuestionsView.as_view(), name="sheet-auto-questions"),
    path("<int:exam_id>/assets/", ExamAssetView.as_view(), name="exam-assets"),
    path("<int:exam_id>/questions/", ExamQuestionsByExamView.as_view(), name="exam-questions-by-exam"),
    path("<int:exam_id>/enrollments/", ExamEnrollmentManageView.as_view(), name="exam-enrollments-manage"),
    path("me/available/", StudentAvailableExamListView.as_view(), name="student-available-exams"),

    # =========================
    # ✅ Template tooling (유지)
    # =========================
    path("<int:exam_id>/builder/", TemplateBuilderView.as_view(), name="template-builder"),
    path("<int:exam_id>/template-status/", TemplateStatusView.as_view(), name="template-status"),
    path("<int:exam_id>/template-editor/", TemplateEditorView.as_view(), name="template-editor"),
    path("<int:exam_id>/template-validation/", TemplateValidationView.as_view(), name="template-validation"),

    # ==================================================
    # ✅ PHASE 2 (추가): 템플릿 → 실제 시험 생성 (기존 create도 그대로 둠)
    # POST /api/v1/exams/<template_exam_id>/spawn-regular/
    # ==================================================
    path(
        "<int:exam_id>/spawn-regular/",
        RegularExamFromTemplateView.as_view(),
        name="spawn-regular-from-template",
    ),

    # ==================================================
    # ✅ PHASE 2 (추가): OMR 시험지 PDF 생성 → ExamAsset(omr_sheet) 저장
    # POST /api/v1/exams/<template_exam_id>/generate-omr/
    # ==================================================
    path(
        "<int:exam_id>/generate-omr/",
        GenerateOMRSheetAssetView.as_view(),
        name="generate-omr-sheet",
    ),
]
