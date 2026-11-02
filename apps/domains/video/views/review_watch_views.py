from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academy.application.use_cases.student_video_access_context import (
    StudentVideoAccessError, ensure_student_video_watch_allowed, resolve_student_video_access_context,
)
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.video.models import Video, VideoReviewWatchStream, VideoReviewWatchUsage
from apps.domains.video.services.review_watch import elapsed_ms, record_review_state, threshold_ms


class ReviewSignalSerializer(serializers.Serializer):
    stream = serializers.UUIDField()
    sequence = serializers.IntegerField(min_value=1, max_value=2**53 - 1)
    playing = serializers.BooleanField()
    enrollment = serializers.IntegerField(min_value=1, required=False)


class ReviewStateSerializer(serializers.Serializer):
    stream = serializers.UUIDField()
    used_ms = serializers.IntegerField()
    remaining_ms = serializers.IntegerField(allow_null=True)


class ReviewStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    used_ms = serializers.IntegerField()
    started_at = serializers.DateTimeField()


class ReviewReportSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    duration = serializers.IntegerField(allow_null=True)
    threshold_ms = serializers.IntegerField()
    students = ReviewStudentSerializer(many=True)


class StudentReviewWatchView(APIView):
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    @extend_schema(request=ReviewSignalSerializer, responses=ReviewStateSerializer)
    def post(self, request, video_id):
        form = ReviewSignalSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        video = get_object_or_404(Video, pk=video_id, tenant=request.tenant)
        student = get_request_student(request)
        try:
            context = resolve_student_video_access_context(
                request, video, explicit_enrollment_id=data.get("enrollment"), allow_public_enrollment_write=False,
            )
            ensure_student_video_watch_allowed(context)
        except StudentVideoAccessError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)
        if not student or context.access_mode_value not in (None, "FREE_REVIEW"):
            return Response({"detail": "복습 권한을 확인할 수 없습니다."}, status=403)
        return Response(record_review_state(
            video=video, student=student, stream_key=data["stream"], sequence=data["sequence"],
            playing=data["playing"],
        ))


class ReviewWatchReportView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=ReviewReportSerializer)
    def get(self, request, video_id):
        video = get_object_or_404(Video, pk=video_id, tenant=request.tenant)
        usages = VideoReviewWatchUsage.objects.filter(video=video, student__tenant=request.tenant).select_related(
            "student",
        ).prefetch_related(Prefetch(
            "streams", queryset=VideoReviewWatchStream.objects.filter(active_since__isnull=False),
            to_attr="playing_streams",
        ))
        now = timezone.now()
        rows = []
        for usage in usages:
            used_ms = usage.used_ms + sum(elapsed_ms(stream, now) for stream in usage.playing_streams)
            if used_ms:
                rows.append({
                    "student_id": usage.student_id, "student_name": usage.student.name,
                    "started_at": usage.started_at, "used_ms": used_ms,
                })
        rows.sort(key=lambda row: (-row["used_ms"], row["student_id"]))
        return Response({
            "enabled": video.review_watch_limit_enabled,
            "threshold_ms": threshold_ms(video),
            "duration": video.duration,
            "students": rows,
        })
