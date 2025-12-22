
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import AnswerKey
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer

class AnswerKeyViewSet(ModelViewSet):
    queryset = AnswerKey.objects.select_related("exam")
    serializer_class = AnswerKeySerializer
    permission_classes = [IsAuthenticated]
