# apps/support/analytics/urls.py
from django.urls import path

from apps.support.analytics.views import (
    ExamAnalyticsSummaryView,
    ExamAnalyticsQuestionStatsView,
    ExamAnalyticsTopWrongView,
    ExamAnalyticsWrongDistributionView,
)

urlpatterns = [
    path("analytics/exams/<int:exam_id>/summary/", ExamAnalyticsSummaryView.as_view()),
    path("analytics/exams/<int:exam_id>/questions/", ExamAnalyticsQuestionStatsView.as_view()),
    path("analytics/exams/<int:exam_id>/top-wrong/", ExamAnalyticsTopWrongView.as_view()),
    path(
        "analytics/exams/<int:exam_id>/questions/<int:question_id>/wrong-distribution/",
        ExamAnalyticsWrongDistributionView.as_view(),
    ),
]
