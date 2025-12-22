from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer

class ExamViewSet(ModelViewSet):
    queryset = Exam.objects.all()
    serializer_class = ExamSerializer
    permission_classes = [IsAuthenticated]
