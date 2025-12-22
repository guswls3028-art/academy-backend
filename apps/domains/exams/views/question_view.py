from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer

class QuestionViewSet(ModelViewSet):
    queryset = ExamQuestion.objects.select_related("sheet", "sheet__exam")
    serializer_class = QuestionSerializer
    permission_classes = [IsAuthenticated]
