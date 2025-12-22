from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend

from .models import Dday
from .serializers import DdaySerializer


class DdayViewSet(ModelViewSet):
    queryset = Dday.objects.all()
    serializer_class = DdaySerializer

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["lecture"]
