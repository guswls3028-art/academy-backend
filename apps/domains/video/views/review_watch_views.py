from types import SimpleNamespace

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from academy.application.use_cases.student_video_access_context import (
    StudentVideoAccessError,
    ensure_student_video_watch_allowed,
    resolve_student_video_access_context,
)
from apps.core.models import OpsAuditLog
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.video.models import Video, VideoReviewWatchUsage
from apps.domains.video.review_serializers import ReviewWatchSummarySerializer
from apps.domains.video.services.review_watch import (
    SIGNAL_TIMEOUT_MS,
    elapsed_ms,
    review_watch_limit_ms,
    review_watch_summary,
    review_watch_summary_with_playing_streams,
    settle,
    threshold_ms,
    with_playing_review_streams,
)
from apps.support.video.review_audience import active_review_students, review_report_recipients


class ReviewStudentSerializer(ReviewWatchSummarySerializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    can_adjust = serializers.BooleanField()


class ReviewReportSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    duration = serializers.IntegerField(allow_null=True)
    threshold_ms = serializers.IntegerField()
    source_type = serializers.CharField()
    updated_at = serializers.DateTimeField()
    students = ReviewStudentSerializer(many=True)


class ReviewAllowanceSerializer(serializers.Serializer):
    limit_ms = serializers.IntegerField(min_value=0, max_value=2**53 - 1, allow_null=True)
    expected_revision = serializers.CharField(max_length=64)


class ReviewWatchReportView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=ReviewReportSerializer)
    def get(self, request, video_id):
        video = get_object_or_404(Video.objects.select_related("session__lecture"), pk=video_id, tenant=request.tenant)
        now = timezone.now()
        recipients = dict(review_report_recipients(video, now).values_list("pk", "name"))
        usages = with_playing_review_streams(
            VideoReviewWatchUsage.objects.filter(video=video, student__tenant=request.tenant).select_related("student"),
        )
        usage_by_student = {usage.student_id: usage for usage in usages}
        rows = []
        for student_id in recipients.keys() | usage_by_student.keys():
            usage = usage_by_student.get(student_id)
            eligible = student_id in recipients
            summary = review_watch_summary_with_playing_streams(video, usage, now=now, applicable=eligible)
            if eligible or summary["used_ms"]:
                rows.append({
                    **summary,
                    "student_id": student_id,
                    "student_name": recipients[student_id] if eligible else usage.student.name,
                    "started_at": usage.started_at if summary["used_ms"] else None,
                    "can_adjust": eligible and bool(video.duration) and video.review_watch_limit_enabled,
                })
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


class ReviewWatchAllowanceView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(request=ReviewAllowanceSerializer, responses={200: ReviewWatchSummarySerializer, 409: ReviewWatchSummarySerializer})
    @transaction.atomic
    def patch(self, request, video_id, student_id):
        data = ReviewAllowanceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        video = get_object_or_404(
            Video.objects.select_related("session__lecture").select_for_update(of=("self",)),
            pk=video_id, tenant=request.tenant, status=Video.Status.READY,
        )
        student = get_object_or_404(active_review_students(video).select_related("user"), pk=student_id)
        principal = SimpleNamespace(tenant=request.tenant, user=student.user, META={})
        try:
            context = resolve_student_video_access_context(principal, video, allow_public_enrollment_write=False)
            ensure_student_video_watch_allowed(context)
        except StudentVideoAccessError:
            return Response({"code": "video_student_unavailable"}, status=status.HTTP_404_NOT_FOUND)

        usage = VideoReviewWatchUsage.objects.select_for_update().filter(video=video, student=student).first()
        now = timezone.now()
        if usage is not None:
            usage.playing_streams = list(usage.streams.filter(active_since__isnull=False, closed=False))
        current = review_watch_summary_with_playing_streams(video, usage, now=now)
        if current["limit_ms"] is None:
            return Response(current, status=status.HTTP_409_CONFLICT)
        new_override = data.validated_data["limit_ms"]
        previous_override = usage.limit_ms_override if usage is not None else None
        if new_override == previous_override:
            return Response(current)
        if data.validated_data["expected_revision"] != current["revision"]:
            return Response(current, status=status.HTTP_409_CONFLICT)
        if usage is None:
            usage, _ = VideoReviewWatchUsage.objects.select_for_update().get_or_create(video=video, student=student)
            usage.playing_streams = list(usage.streams.filter(active_since__isnull=False, closed=False))

        now = timezone.now()
        for stream in usage.playing_streams:
            # Preserve already elapsed viewing under the previous allowance.
            still_playing = elapsed_ms(stream, now) < SIGNAL_TIMEOUT_MS and stream.access_until > now
            settle(video, usage, stream, now)
            stream.active_since = now if still_playing else None
        usage.limit_ms_override = new_override
        usage.allowance_revision += 1
        usage.save(update_fields=["used_ms", "prefetch_credit_ms", "limit_ms_override", "allowance_revision"])
        limit = review_watch_limit_ms(video, usage)
        for stream in usage.playing_streams:
            if limit is not None and usage.used_ms >= limit:
                stream.closed = True
                stream.active_since = None
            stream.save(update_fields=["active_since", "closed"])
        updated = review_watch_summary(video, usage)
        OpsAuditLog.objects.create(
            action="video.review_watch.allowance.update",
            actor_user=request.user,
            actor_username=request.user.get_username(),
            target_tenant=request.tenant,
            target_user=student.user,
            payload={
                "video_id": video.pk, "student_id": student.pk,
                "previous_limit_ms": current["limit_ms"], "limit_ms": updated["limit_ms"],
                "previous_override_ms": previous_override, "override_ms": new_override,
                "revision": updated["revision"],
            },
        )
        return Response(updated)
