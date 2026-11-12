"""Teacher operations for using one Academy upload in multiple lessons."""

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, ValidationError

from academy.adapters.db.django import repositories_video as video_repo
from apps.core.r2_paths import video_hls_master_path
from apps.core.services.ops_audit import record_audit
from apps.domains.video.models import Video


class VideoPlacementItemSerializer(serializers.Serializer):
    video_id = serializers.IntegerField()
    lecture_id = serializers.IntegerField()
    lecture_title = serializers.CharField()
    session_id = serializers.IntegerField()
    session_title = serializers.CharField()
    is_original = serializers.BooleanField()


class VideoPlacementsSerializer(serializers.Serializer):
    source_video_id = serializers.IntegerField()
    can_attach = serializers.BooleanField()
    placements = VideoPlacementItemSerializer(many=True)


class VideoPlacementChangeSerializer(serializers.Serializer):
    add_session_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, min_length=1, max_length=50,
    )
    remove_video_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, min_length=1, max_length=50,
    )

    def validate(self, attrs):
        if len(attrs) != 1:
            raise ValidationError("추가하거나 연결을 해제할 차시를 선택해 주세요.")
        selected = next(iter(attrs.values()))
        if len(set(selected)) != len(selected):
            raise ValidationError("같은 차시를 중복 선택할 수 없습니다.")
        return attrs


class PlacementAuditUnavailable(APIException):
    status_code = 503
    default_detail = "변경을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _source_for(video, *, lock=False):
    roots = Video.all_with_deleted.filter(
        tenant_id=video.tenant_id, pk=video.reused_from_id or video.pk, reused_from_id__isnull=True,
    )
    if not video.reused_from_id:
        roots = roots.filter(deleted_at__isnull=True)
    if lock:
        roots = roots.select_for_update()
    source = roots.first()
    if source is None:
        raise NotFound("이 영상의 원본을 찾을 수 없습니다.")
    return source


def _is_reusable(source):
    return (
        source.source_type == Video.SourceType.UPLOADED
        and source.status == Video.Status.READY
        and source.hls_path == video_hls_master_path(source.tenant_id, source.pk)
    )


def _placements(source):
    return (
        Video.objects.filter(tenant_id=source.tenant_id, session__lecture__tenant_id=source.tenant_id)
        .filter(Q(pk=source.pk) | Q(reused_from_id=source.pk))
        .select_related("session__lecture", "session__section")
        .order_by("session__lecture__title", "session__order", "pk")
    )


def _placement_data(source):
    return {
        "source_video_id": source.pk,
        "can_attach": _is_reusable(source),
        "placements": [
            {
                "video_id": video.pk,
                "lecture_id": video.session.lecture_id,
                "lecture_title": video.session.lecture.title,
                "session_id": video.session_id,
                "session_title": str(video.session.display_label) + (
                    f" ({video.session.section.label}반)" if video.session.section_id else ""
                ),
                "is_original": video.pk == source.pk,
            }
            for video in _placements(source)
        ],
    }


def list_video_placements(video):
    return _placement_data(_source_for(video))


@transaction.atomic
def change_video_placements(request, video, changes):
    add_ids = changes.get("add_session_ids", [])
    remove_ids = changes.get("remove_video_ids", [])
    # Use the same parent lock as upload/reorder, in stable order for bulk adds.
    sessions = list(
        video_repo.session_all_queryset().select_for_update(of=("self",))
        .filter(pk__in=add_ids, lecture__tenant=request.tenant, lecture__is_active=True, lecture__is_system=False)
        .order_by("pk")
    ) if add_ids else []
    if len(sessions) != len(add_ids):
        raise NotFound("선택한 차시 중 연결할 수 없는 차시가 있습니다.")
    source = _source_for(video, lock=True)
    if not Video.objects.filter(pk=video.pk, tenant=request.tenant).exists():
        raise NotFound("이 영상의 연결을 찾을 수 없습니다.")

    changed_ids = []
    if add_ids:
        if not _is_reusable(source):
            raise ValidationError("재생 준비가 완료된 학원 영상만 연결할 수 있습니다.")
        used = set(_placements(source).filter(session_id__in=add_ids).values_list("session_id", flat=True))
        for session in sessions:
            if session.pk in used:
                continue
            order = Video.objects.filter(
                tenant=request.tenant, session=session, folder__isnull=True,
            ).aggregate(last=Max("order"))["last"] or 0
            placement = Video.objects.create(
                tenant=request.tenant,
                session=session,
                title=source.title,
                reused_from=source,
                status=Video.Status.READY,
                hls_path=source.hls_path,
                duration=source.duration,
                order=order + 1,
                allow_skip=video.allow_skip,
                max_speed=video.max_speed,
                show_watermark=video.show_watermark,
                review_watch_limit_enabled=video.review_watch_limit_enabled,
            )
            changed_ids.append(placement.pk)
    else:
        links = Video.objects.select_for_update().filter(
            pk__in=remove_ids, tenant=request.tenant, reused_from_id=source.pk,
        ).order_by("pk")
        changed_ids = list(links.values_list("pk", flat=True))
        if len(changed_ids) != len(remove_ids):
            raise NotFound("선택한 항목 중 해제할 수 없는 연결이 있습니다.")
        now = timezone.now()
        Video.objects.filter(pk__in=changed_ids).update(deleted_at=now, updated_at=now)

    if changed_ids and record_audit(
        request,
        action="video.attach_placements" if add_ids else "video.detach_placements",
        summary="영상 사용 차시 변경",
        target_tenant=request.tenant,
        payload={"source_video_id": source.pk, "video_ids": changed_ids},
    ) is None:
        raise PlacementAuditUnavailable()
    return _placement_data(source)
