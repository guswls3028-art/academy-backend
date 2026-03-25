# PATH: apps/domains/submissions/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SubmissionViewSet
from .views.exam_omr_submit_view import ExamOMRSubmitView

# ✅ 추가
from .views.exam_submissions_list_view import ExamSubmissionsListView
from .views.homework_submissions_list_view import HomeworkSubmissionsListView
from .views.exam_omr_batch_upload_view import ExamOMRBatchUploadView
from .views.pending_submissions_view import PendingSubmissionsView

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

    # ✅ 다건 업로드: POST /submissions/exams/{examId}/omr/batch/
    path(
        "submissions/exams/<int:exam_id>/omr/batch/",
        ExamOMRBatchUploadView.as_view(),
        name="exam-omr-batch-upload",
    ),
]

urlpatterns += router.urls
