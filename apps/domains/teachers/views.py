# PATH: apps/domains/teachers/views.py
from rest_framework.viewsets import ModelViewSet
from .models import Teacher
from .serializers import TeacherSerializer


class TeacherViewSet(ModelViewSet):
    serializer_class = TeacherSerializer

    def get_queryset(self):
        return Teacher.objects.filter(tenant=self.request.tenant)
