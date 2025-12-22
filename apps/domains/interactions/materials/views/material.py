from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend

from ..models import Material
from ..serializers import MaterialSerializer


class MaterialViewSet(ModelViewSet):
    queryset = Material.objects.all().select_related(
        "lecture",
        "category",
        "uploaded_by",
    )
    serializer_class = MaterialSerializer
    parser_classes = [MultiPartParser, FormParser]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "category"]
    search_fields = ["title"]
