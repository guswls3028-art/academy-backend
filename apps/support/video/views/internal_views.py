# PATH: apps/support/video/views/internal_views.py

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


class VideoProcessingCompleteView(APIView):
    """
    worker → API ACK
    """
    permission_classes = [AllowAny]

    def post(self, request, video_id: int):
        token = request.headers.get("X-Worker-Token")
        if token != settings.INTERNAL_WORKER_TOKEN:
            return Response(status=403)

        return Response({"status": "ack"}, status=200)
