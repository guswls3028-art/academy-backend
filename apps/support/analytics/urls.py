# apps/support/analytics/urls.py
from django.urls import path

from apps.support.analytics.views import (
    ExamAnalyticsSummaryView,
    ExamAnalyticsQuestionStatsView,
    ExamAnalyticsTopWrongView,
    ExamAnalyticsWrongDistributionView,
    ExamAnalyticsResultsView,   # ✅ 신규
)

urlpatterns = [
    # ================= 시험 요약 =================
    path(
        "analytics/exams/<int:exam_id>/summary/",
        ExamAnalyticsSummaryView.as_view(),
    ),

    # ================= 문항별 통계 =================
    path(
        "analytics/exams/<int:exam_id>/questions/",
        ExamAnalyticsQuestionStatsView.as_view(),
    ),

    path(
        "analytics/exams/<int:exam_id>/top-wrong/",
        ExamAnalyticsTopWrongView.as_view(),
    ),

    path(
        "analytics/exams/<int:exam_id>/questions/<int:question_id>/wrong-distribution/",
        ExamAnalyticsWrongDistributionView.as_view(),
    ),

    # ================= 관리자 성적 리스트 =================
    path(
        "analytics/exams/<int:exam_id>/results/",
        ExamAnalyticsResultsView.as_view(),
    ),
]
