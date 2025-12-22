from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Sheet
from apps.domains.exams.serializers.sheet import SheetSerializer

class SheetViewSet(ModelViewSet):
    queryset = Sheet.objects.select_related("exam")
    serializer_class = SheetSerializer
    permission_classes = [IsAuthenticated]
