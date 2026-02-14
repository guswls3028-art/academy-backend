# PATH: apps/domains/inventory/urls.py

from django.urls import path
from . import views

urlpatterns = [
    path("quota/", views.QuotaView.as_view()),
    path("inventory/", views.InventoryListView.as_view()),
    path("inventory/folders/", views.FolderCreateView.as_view()),
    path("inventory/upload/", views.FileUploadView.as_view()),
    path("inventory/folders/<int:folder_id>/", views.FolderDeleteView.as_view()),
    path("inventory/files/<int:file_id>/", views.FileDeleteView.as_view()),
    path("inventory/presign/", views.PresignView.as_view()),
    path("inventory/move/", views.MoveView.as_view()),
]
