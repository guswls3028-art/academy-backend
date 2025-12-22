from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend

from ..models import MaterialCategory
from ..serializers import MaterialCategorySerializer


class MaterialCategoryViewSet(ModelViewSet):
    queryset = MaterialCategory.objects.all()
    serializer_class = MaterialCategorySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["lecture"]
