# apps/support/media/views/permission_views.py

# test change

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoPermission
from ..serializers import VideoPermissionSerializer


class VideoPermissionViewSet(ModelViewSet):
    queryset = VideoPermission.objects.all()
    serializer_class = VideoPermissionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="bulk-set")
    def bulk_set(self, request):
        """
        payload:
        {
          "video": 1,
          "enrollments": [3,4,5],
          "rule": "blocked"
        }
        """
        video_id = request.data.get("video")
        enrollments = request.data.get("enrollments", [])
        rule = request.data.get("rule")

        if not video_id or not enrollments or not rule:
            return Response(
                {"detail": "video, enrollments, rule required"},
                status=400,
            )

        objs = []
        for enrollment_id in enrollments:
            obj, _ = VideoPermission.objects.update_or_create(
                video_id=video_id,
                enrollment_id=enrollment_id,
                defaults={"rule": rule},
            )
            objs.append(obj)

        return Response(
            VideoPermissionSerializer(objs, many=True).data,
            status=status.HTTP_200_OK,
        )