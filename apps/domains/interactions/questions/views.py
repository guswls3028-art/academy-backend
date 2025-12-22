from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend

from .models import Question, Answer
from .serializers import QuestionSerializer, AnswerSerializer


class QuestionViewSet(ModelViewSet):
    queryset = Question.objects.all().select_related(
        "enrollment",
        "enrollment__student",
    )
    serializer_class = QuestionSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["enrollment"]
    search_fields = ["title", "content", "enrollment__student__name"]


class AnswerViewSet(ModelViewSet):
    queryset = Answer.objects.all().select_related("question")
    serializer_class = AnswerSerializer

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["question"]
