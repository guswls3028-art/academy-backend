# PATH: apps/domains/teachers/views.py
from rest_framework.viewsets import ModelViewSet
from .serializers import TeacherSerializer
from academy.adapters.db.django import repositories_teachers as teacher_repo


class TeacherViewSet(ModelViewSet):
    serializer_class = TeacherSerializer

    def get_queryset(self):
        return teacher_repo.teacher_filter_tenant(self.request.tenant)
