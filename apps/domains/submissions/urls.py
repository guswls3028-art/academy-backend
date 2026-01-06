# apps/domains/submissions/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SubmissionViewSet
from .views.exam_omr_submit_view import ExamOMRSubmitView

router = DefaultRouter()
router.register("submissions", SubmissionViewSet, basename="submissions")

urlpatterns = [
    # 🔥 STEP 2: 시험 OMR 전용 제출
    # ⚠️ TEMPORARY API (STEP 2 전용)
    # - REST 정규 경로는 추후 /submissions/ 통합 예정
    # - 현재는 OMR 전용 UX 흐름 분리를 위해 유지
    path(
        "submissions/exams/<int:exam_id>/omr/",
        ExamOMRSubmitView.as_view(),
        name="exam-omr-submit",
    ),
]

urlpatterns += router.urls
