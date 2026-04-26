# PATH: apps/domains/matchup/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Documents
    path("documents/upload/", views.DocumentUploadView.as_view()),
    path("documents/promote/", views.DocumentPromoteFromInventoryView.as_view()),
    path("documents/", views.DocumentListView.as_view()),
    path("documents/<int:doc_id>/", views.DocumentDetailView.as_view()),
    path("documents/<int:doc_id>/preview/", views.DocumentPreviewView.as_view()),
    path("documents/<int:doc_id>/cross-matches/", views.DocumentCrossMatchesView.as_view()),
    path("documents/<int:doc_id>/job/", views.DocumentJobView.as_view()),
    path("documents/<int:doc_id>/retry/", views.DocumentRetryView.as_view()),

    # Problems
    path("problems/", views.ProblemListView.as_view()),
    path("problems/presign/", views.ProblemPresignView.as_view()),
    path("problems/<int:problem_id>/", views.ProblemDetailView.as_view()),
    path("problems/<int:problem_id>/similar/", views.SimilarProblemView.as_view()),
]
