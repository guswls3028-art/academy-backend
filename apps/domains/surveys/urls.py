from django.urls import path

from . import views

urlpatterns = [
    path("", views.SurveyListView.as_view()),
    path("student-choices/", views.SurveyStudentChoicesView.as_view()),
    path("student/", views.StudentSurveyListView.as_view()),
    path("student/<int:survey_id>/", views.StudentSurveyDetailView.as_view()),
    path("student/<int:survey_id>/response/", views.StudentSurveyResponseView.as_view()),
    path("students/<int:student_id>/responses/", views.StudentSurveyHistoryView.as_view()),
    path("<int:survey_id>/", views.SurveyDetailView.as_view()),
    path("<int:survey_id>/publish/", views.SurveyPublishView.as_view()),
    path("<int:survey_id>/close/", views.SurveyCloseView.as_view()),
    path("<int:survey_id>/responses/", views.SurveyResponsesView.as_view()),
    path("<int:survey_id>/statistics/", views.SurveyStatisticsView.as_view()),
    path("<int:survey_id>/export/", views.SurveyExportView.as_view()),
]
