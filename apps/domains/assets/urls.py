# apps/domains/assets/urls.py
from django.urls import path, include

urlpatterns = [
    path("omr/", include("apps.domains.assets.omr.urls")),
]

