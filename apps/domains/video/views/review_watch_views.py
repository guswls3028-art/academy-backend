from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.video.models import Video, VideoReviewWatchStream, VideoReviewWatchUsage
from apps.domains.video.services.review_watch import chargeable_ms, elapsed_ms, threshold_ms


class ReviewStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    used_ms = serializers.IntegerField()
    started_at = serializers.DateTimeField()


class ReviewReportSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    duration = serializers.IntegerField(allow_null=True)
    threshold_ms = serializers.IntegerField()
    source_type = serializers.CharField()
    updated_at = serializers.DateTimeField()
    students = ReviewStudentSerializer(many=True)


class ReviewWatchReportView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=ReviewReportSerializer)
    def get(self, request, video_id):
        video = get_object_or_404(Video, pk=video_id, tenant=request.tenant)
        usages = (
            VideoReviewWatchUsage.objects.filter(video=video, student__tenant=request.tenant)
            .select_related(
                "student",
            )
            .prefetch_related(
                Prefetch(
                    "streams",
                    queryset=VideoReviewWatchStream.objects.filter(active_since__isnull=False),
                    to_attr="playing_streams",
                )
            )
        )
        now = timezone.now()
        rows = []
        for usage in usages:
            pending = sum(elapsed_ms(stream, now) for stream in usage.playing_streams)
            used_ms = usage.used_ms + chargeable_ms(video, usage.used_ms, pending)
            if used_ms:
                rows.append(
                    {
                        "student_id": usage.student_id,
                        "student_name": usage.student.name,
                        "started_at": usage.started_at,
                        "used_ms": used_ms,
                    }
                )
        rows.sort(key=lambda row: (-row["used_ms"], row["student_id"]))
        return Response(
            {
                "enabled": video.review_watch_limit_enabled,
                "threshold_ms": threshold_ms(video),
                "source_type": video.source_type,
                "updated_at": serializers.DateTimeField().to_representation(video.updated_at),
                "duration": video.duration,
                "students": rows,
            }
        )
