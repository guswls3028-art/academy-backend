# PATH: apps/domains/submissions/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SubmissionViewSet
from .views.exam_omr_submit_view import ExamOMRSubmitView

# ✅ 추가
from .views.exam_submissions_list_view import ExamSubmissionsListView
from .views.exam_omr_batch_upload_view import ExamOMRBatchUploadView

router = DefaultRouter()
router.register("submissions", SubmissionViewSet, basename="submissions")

urlpatterns = [
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

    # ✅ 다건 업로드: POST /submissions/exams/{examId}/omr/batch/
    path(
        "submissions/exams/<int:exam_id>/omr/batch/",
        ExamOMRBatchUploadView.as_view(),
        name="exam-omr-batch-upload",
    ),
]

urlpatterns += router.urls
