# PATH: apps/domains/tools/urls.py
# 도구 API — PPT 생성 등 선생님 편의 도구

from django.urls import path
from .ppt.views import PptGenerateView

urlpatterns = [
    path("ppt/generate/", PptGenerateView.as_view(), name="tools-ppt-generate"),
]
