from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response

from .models import (
    BoardCategory,
    BoardPost,
    BoardAttachment,
    BoardReadStatus,
)
from .serializers import (
    BoardCategorySerializer,
    BoardPostSerializer,
    BoardReadStatusSerializer,
)


class BoardCategoryViewSet(ModelViewSet):
    queryset = BoardCategory.objects.all()
    serializer_class = BoardCategorySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["lecture"]


class BoardPostViewSet(ModelViewSet):
    queryset = BoardPost.objects.all().select_related("lecture", "category")
    serializer_class = BoardPostSerializer
    parser_classes = [MultiPartParser, FormParser]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "category"]
    search_fields = ["title", "content"]

    def create(self, request, *args, **kwargs):
        files = request.FILES.getlist("files")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save()

        for f in files:
            BoardAttachment.objects.create(post=post, file=f)

        return Response(self.get_serializer(post).data, status=201)


class BoardReadStatusViewSet(ModelViewSet):
    queryset = BoardReadStatus.objects.all().select_related(
        "post",
        "enrollment",
        "enrollment__student",
    )
    serializer_class = BoardReadStatusSerializer

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["post", "enrollment"]

    @action(detail=False, methods=["post"])
    def mark_read(self, request):
        obj, _ = BoardReadStatus.objects.get_or_create(
            post_id=request.data.get("post"),
            enrollment_id=request.data.get("enrollment"),
        )
        return Response(BoardReadStatusSerializer(obj).data)
