from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend

from .models import Counseling
from .serializers import CounselingSerializer


class CounselingViewSet(ModelViewSet):
    queryset = Counseling.objects.all().select_related(
        "enrollment",
        "enrollment__student",
    )
    serializer_class = CounselingSerializer

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["enrollment"]
