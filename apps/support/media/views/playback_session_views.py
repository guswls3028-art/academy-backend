# apps/support/media/views/playback_session_views.py

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import IsStudent
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import Video
from ..serializers import PlaybackSessionSerializer
from ..services.playback_session import create_playback_session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment


class PlaybackSessionView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request):
        student = request.user.student_profile
        video_id = request.data.get("video_id")

        if not video_id:
            raise ValidationError("video_id is required")

        video = get_object_or_404(Video, id=video_id)
        lecture = video.session.lecture

        enrollment = Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            status="ACTIVE",
        ).first()

        if not enrollment:
            raise PermissionDenied("Not enrolled")

        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            raise PermissionDenied("No session access")

        result = create_playback_session(
            user=request.user,
            video_id=video.id,
            enrollment_id=enrollment.id,
        )

        if not result.get("ok"):
            return Response({"detail": result["error"]}, status=409)

        return Response(PlaybackSessionSerializer(result).data, status=201)
