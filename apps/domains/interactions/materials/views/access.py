from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend

from ..models import MaterialAccess
from ..serializers import MaterialAccessSerializer


class MaterialAccessViewSet(ModelViewSet):
    """
    강사가 특정 자료 접근 권한을 제어
    """
    queryset = MaterialAccess.objects.all().select_related(
        "material",
        "student",
        "enrollment",
        "session",
    )
    serializer_class = MaterialAccessSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["material", "student", "enrollment", "session"]
