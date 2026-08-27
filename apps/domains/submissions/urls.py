# PATH: apps/domains/submissions/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SubmissionViewSet
from .views.exam_omr_submit_view import ExamOMRSubmitView

# ✅ 추가
from .views.exam_submissions_list_view import ExamSubmissionsListView
from .views.homework_submissions_list_view import HomeworkSubmissionsListView
from .views.exam_omr_batch_upload_view import (
    ExamOMRBatchInitializeView,
    ExamOMRBatchUploadView,
    OmrUploadBatchCompletionClaimView,
    OmrUploadBatchDetailView,
    OmrUploadBatchListView,
    OmrUploadBatchRetryView,
)
from .views.pending_submissions_view import (
    PendingSubmissionPreviewView,
    PendingSubmissionsView,
)
from .views.exam_candidates_view import ExamCandidatesView
from .views.homework_candidates_view import HomeworkCandidatesView
from .views.homework_submission_media_view import (
    HomeworkSubmissionMediaCollectionView,
    HomeworkSubmissionMediaDetailView,
    HomeworkSubmissionMediaPreviewView,
)

router = DefaultRouter()
router.register("submissions", SubmissionViewSet, basename="submissions")

urlpatterns = [
    # ✅ 관리자 제출 인박스: GET /api/v1/submissions/submissions/pending/
    # ⚠️ router.urls 보다 먼저 선언해야 router의 detail view에 "pending"가 잡히지 않음
    path(
        "submissions/pending/",
        PendingSubmissionsView.as_view(),
        name="pending-submissions",
    ),
    path(
        "submissions/<int:submission_id>/preview/",
        PendingSubmissionPreviewView.as_view(),
        name="pending-submission-preview",
    ),

    # 🔥 STEP 2: 시험 OMR 전용 제출 (file_key 기반)
    path(
        "submissions/exams/<int:exam_id>/omr/",
        ExamOMRSubmitView.as_view(),
        name="exam-omr-submit",
    ),

    # ✅ 프론트 제출 목록: GET /submissions/exams/{examId}/
    path(
        "submissions/exams/<int:exam_id>/",
        ExamSubmissionsListView.as_view(),
        name="exam-submissions-list",
    ),

    # ✅ 과제 제출 목록: GET /submissions/homework/{homeworkId}/
    path(
        "submissions/homework/<int:homework_id>/",
        HomeworkSubmissionsListView.as_view(),
        name="homework-submissions-list",
    ),
    path(
        "submissions/homework/<int:homework_id>/media/",
        HomeworkSubmissionMediaCollectionView.as_view(),
        name="homework-submission-media-collection",
    ),
    path(
        "submissions/homework/<int:homework_id>/media/<str:media_id>/",
        HomeworkSubmissionMediaDetailView.as_view(),
        name="homework-submission-media-detail",
    ),
    path(
        "submissions/homework/<int:homework_id>/media/<str:media_id>/preview/",
        HomeworkSubmissionMediaPreviewView.as_view(),
        name="homework-submission-media-preview",
    ),

    # ✅ 다건 업로드: POST /submissions/exams/{examId}/omr/batch/
    path(
        "submissions/exams/<int:exam_id>/omr/batch/",
        ExamOMRBatchUploadView.as_view(),
        name="exam-omr-batch-upload",
    ),
    path(
        "submissions/exams/<int:exam_id>/omr/batches/",
        ExamOMRBatchInitializeView.as_view(),
        name="exam-omr-batch-initialize",
    ),
    path(
        "submissions/omr/batches/",
        OmrUploadBatchListView.as_view(),
        name="omr-upload-batch-list",
    ),
    path(
        "submissions/omr/batches/<uuid:batch_id>/",
        OmrUploadBatchDetailView.as_view(),
        name="omr-upload-batch-detail",
    ),
    path(
        "submissions/omr/batches/<uuid:batch_id>/retry/",
        OmrUploadBatchRetryView.as_view(),
        name="omr-upload-batch-retry",
    ),
    path(
        "submissions/omr/batches/<uuid:batch_id>/claim-completion/",
        OmrUploadBatchCompletionClaimView.as_view(),
        name="omr-upload-batch-claim-completion",
    ),

    # ✅ OMR 검토 학생 picker: GET /submissions/exams/{examId}/candidates/?q=검색어
    path(
        "submissions/exams/<int:exam_id>/candidates/",
        ExamCandidatesView.as_view(),
        name="exam-candidates",
    ),

    # ✅ Homework 검토 학생 picker: GET /submissions/homework/{homeworkId}/candidates/?q=검색어
    path(
        "submissions/homework/<int:homework_id>/candidates/",
        HomeworkCandidatesView.as_view(),
        name="homework-candidates",
    ),
]

urlpatterns += router.urls
