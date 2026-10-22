from django.db import transaction
from django.db.models import BigIntegerField, Case, DateTimeField, F, Q, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved
from apps.domains.landing_public.models.video import PublicVideo, PublicVideoEvent, PublicVideoShowcase
from apps.domains.landing_public.services.publishing import can_publish, require_publisher
from apps.domains.landing_public.services.youtube import YouTubeUnavailable, normalize_youtube_url, resolve_youtube
from apps.domains.landing_public.services.video_statistics import STATISTICS_MAX_AGE, public_statistics, refresh_video_statistics


class VideoConflict(APIException):
    status_code = 409
    default_detail = "다른 게시자가 내용을 변경했습니다. 입력을 유지한 채 최신 내용을 확인해주세요."
    default_code = "video_conflict"


class VideoContentSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=90)
    summary = serializers.CharField(max_length=220, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=["interview", "lesson", "news"], default="interview")
    category_label = serializers.CharField(max_length=40, allow_blank=True, required=False)
    format = serializers.ChoiceField(choices=["landscape", "vertical"])


class VideoCreateSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)
    request_id = serializers.UUIDField()
    draft_content = VideoContentSerializer()


class VideoEditSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    draft_content = VideoContentSerializer()


class VideoRevisionSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    expected_showcase_revision = serializers.IntegerField(min_value=0)


class VideoPublishSerializer(VideoRevisionSerializer):
    set_as_hero = serializers.BooleanField(default=False)


class VideoShowcaseSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=0)
    hero_video_id = serializers.UUIDField(allow_null=True, required=False)
    title = serializers.CharField(max_length=60, required=False)
    description = serializers.CharField(max_length=220, allow_blank=True, required=False)


class VideoSourceSerializer(serializers.Serializer):
    youtube_video_id = serializers.CharField()
    canonical_url = serializers.URLField()
    source_title = serializers.CharField()
    channel_title = serializers.CharField()
    thumbnail_url = serializers.URLField()


class PublicVideoSerializer(VideoContentSerializer, VideoSourceSerializer):
    id = serializers.UUIDField()
    published_at = serializers.DateTimeField()
    youtube_view_count = serializers.IntegerField(allow_null=True)
    youtube_published_at = serializers.DateTimeField(allow_null=True)
    youtube_statistics_checked_at = serializers.DateTimeField(allow_null=True)


class VideoBrowseSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["landscape", "vertical"], required=False)
    sort = serializers.ChoiceField(choices=["latest", "popular", "oldest"], default="latest")
    q = serializers.CharField(max_length=90, allow_blank=True, default="")


class ManagedVideoSerializer(VideoSourceSerializer):
    id = serializers.UUIDField()
    draft_content = VideoContentSerializer()
    published_content = VideoContentSerializer(allow_null=True)
    status = serializers.ChoiceField(choices=["draft", "published", "unpublished"])
    revision = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    published_at = serializers.DateTimeField(allow_null=True)
    metadata_ready = serializers.BooleanField()


class PublicVideoPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PublicVideoSerializer(many=True)


class ManagedVideoPageSerializer(PublicVideoPageSerializer):
    results = ManagedVideoSerializer(many=True)


class ManagedShowcaseSerializer(serializers.Serializer):
    hero_video_id = serializers.UUIDField(allow_null=True)
    revision = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()


class VideoPublicationSerializer(serializers.Serializer):
    video = ManagedVideoSerializer()
    showcase_revision = serializers.IntegerField()


def validated(serializer_type, data):
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def public_video(video):
    return {
        "id": str(video.id), "youtube_video_id": video.youtube_video_id,
        "canonical_url": video.canonical_url, "source_title": video.source_title,
        "channel_title": video.channel_title, "thumbnail_url": video.thumbnail_url,
        "published_at": video.published_at, **video.published_content, **public_statistics(video),
    }


def managed_video(video):
    return {
        "id": str(video.id), "youtube_video_id": video.youtube_video_id,
        "canonical_url": video.canonical_url, "source_title": video.source_title,
        "channel_title": video.channel_title, "thumbnail_url": video.thumbnail_url,
        "draft_content": video.draft_content, "published_content": video.published_content,
        "status": video.status, "revision": video.revision,
        "created_at": video.created_at, "updated_at": video.updated_at, "published_at": video.published_at,
        "metadata_ready": video.metadata_checked_at is not None,
    }


def check_revision(actual, expected):
    if actual != expected:
        raise VideoConflict()


def showcase_for_write(tenant):
    # Every writer already holds the tenant's publisher-access row lock.
    row, _ = PublicVideoShowcase.objects.get_or_create(tenant=tenant)
    return row


def showcase_content(row):
    return {"title": row.title if row else "영상", "description": row.description if row else ""}


def managed_showcase(row):
    return {"hero_video_id": str(row.hero_video_id) if row and row.hero_video_id else None,
            "revision": row.revision if row else 0, **showcase_content(row)}


def record_event(request, action, video, detail=None):
    PublicVideoEvent.objects.create(
        tenant=request.tenant, actor=request.user, video=video, action=action,
        video_revision=video.revision if video else 0, detail=detail or {},
    )


def update_hero(request, showcase, video):
    new_id = video.id if video else None
    if showcase.hero_video_id == new_id:
        return
    previous = str(showcase.hero_video_id) if showcase.hero_video_id else None
    showcase.hero_video = video
    showcase.revision += 1
    showcase.save(update_fields=["hero_video", "revision"])
    record_event(request, "hero", video, {"previous_id": previous, "hero_id": str(new_id) if new_id else None})


class VideoPage(PageNumberPagination):
    page_size = 12


class VideoAPIView(APIView):
    permission_classes = [TenantResolved]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.tenant.is_active:
            raise NotFound()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def videos(self, request):
        return PublicVideo.objects.filter(tenant=request.tenant).exclude(status="deleted")

    def published(self, request):
        return self.videos(request).filter(status="published", published_content__isnull=False)


class PublicVideoListView(VideoAPIView):
    @extend_schema(auth=[], operation_id="public_videos_list", responses=PublicVideoPageSerializer,
                   parameters=[OpenApiParameter("page", type=int), VideoBrowseSerializer])
    def get(self, request):
        filters = validated(VideoBrowseSerializer, request.query_params)
        rows = self.published(request)
        if filters.get("kind"):
            rows = rows.filter(published_content__format=filters["kind"])
        if filters["q"]:
            rows = rows.filter(Q(published_content__title__icontains=filters["q"])
                               | Q(published_content__category_label__icontains=filters["q"])
                               | Q(channel_title__icontains=filters["q"]))
        fresh = Q(youtube_statistics_checked_at__gte=timezone.now() - STATISTICS_MAX_AGE)
        rows = rows.annotate(
            sort_count=Case(When(fresh, then=F("youtube_view_count")), output_field=BigIntegerField()),
            sort_date=Coalesce(
                Case(When(fresh, then=F("youtube_published_at")), output_field=DateTimeField()),
                "published_at",
            ),
        )
        if filters["sort"] == "popular":
            if rows.exists() and not rows.filter(sort_count__isnull=False).exists():
                raise YouTubeUnavailable("조회수를 확인하지 못했습니다. 잠시 후 다시 시도하거나 최신순으로 영상을 확인해주세요.")
            rows = rows.order_by(F("sort_count").desc(nulls_last=True), "-sort_date", "id")
        else:
            rows = rows.order_by("sort_date" if filters["sort"] == "oldest" else "-sort_date", "id")
        paginator = VideoPage()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([public_video(row) for row in page])


class PublicVideoDetailView(VideoAPIView):
    @extend_schema(auth=[], operation_id="public_videos_detail", responses=PublicVideoSerializer)
    def get(self, request, pk):
        return Response(public_video(get_object_or_404(self.published(request), pk=pk)))


class PublicVideoShowcaseView(VideoAPIView):
    @extend_schema(auth=[], responses=inline_serializer("PublicVideoHero", fields={
        "hero": PublicVideoSerializer(allow_null=True), "title": serializers.CharField(), "description": serializers.CharField(),
    }))
    def get(self, request):
        showcase = PublicVideoShowcase.objects.filter(tenant=request.tenant).first()
        hero = self.published(request).filter(pk=showcase.hero_video_id).first() if showcase else None
        return Response({"hero": public_video(hero) if hero else None, **showcase_content(showcase)})


class PublicVideoCapabilityView(VideoAPIView):
    @extend_schema(responses=inline_serializer("VideoCapability", fields={"can_publish": serializers.BooleanField()}))
    def get(self, request):
        return Response({"can_publish": can_publish(request)})


class PublicVideoResolveView(VideoAPIView):
    @extend_schema(request=inline_serializer("VideoResolveRequest", fields={"url": serializers.URLField()}),
                   responses=inline_serializer("VideoResolveResult", fields={
                       **VideoSourceSerializer().fields, "format": serializers.ChoiceField(choices=["landscape", "vertical"]),
                   }))
    def post(self, request):
        require_publisher(request)
        return Response(resolve_youtube(request.data.get("url", "")))


class ManageVideoListView(VideoAPIView):
    @extend_schema(operation_id="managed_videos_list", responses=ManagedVideoPageSerializer,
                   parameters=[OpenApiParameter("page", type=int)])
    def get(self, request):
        require_publisher(request)
        paginator = VideoPage()
        page = paginator.paginate_queryset(self.videos(request).order_by("-created_at", "id"), request)
        return paginator.get_paginated_response([managed_video(row) for row in page])

    @extend_schema(request=VideoCreateSerializer, responses={201: ManagedVideoSerializer, 200: ManagedVideoSerializer})
    def post(self, request):
        require_publisher(request)
        data = validated(VideoCreateSerializer, request.data)
        normalized = normalize_youtube_url(data["url"])
        metadata = None
        # A retry can recover the saved create without depending on YouTube availability.
        if not PublicVideo.objects.filter(tenant=request.tenant, request_id=data["request_id"]).exists():
            try:
                metadata = resolve_youtube(data["url"])
            except YouTubeUnavailable:
                pass  # A visible metadata_ready=false draft; publication still requires lookup success.
        with transaction.atomic():
            require_publisher(request, lock=True)
            existing = PublicVideo.objects.filter(tenant=request.tenant, request_id=data["request_id"]).first()
            if existing:
                if existing.status == "deleted":
                    raise VideoConflict("삭제된 영상의 등록 요청입니다. 새 영상 등록에서 다시 시작해주세요.")
                if (existing.created_by_id != request.user.id or existing.creation_content != data["draft_content"]
                        or existing.youtube_video_id != normalized["youtube_video_id"]):
                    raise VideoConflict("다른 등록 내용에 이미 사용한 요청입니다. 새 등록을 시작해주세요.")
                return Response(managed_video(existing))
            duplicate = self.videos(request).filter(youtube_video_id=normalized["youtube_video_id"]).first()
            if duplicate:
                return Response({"detail": "이미 등록된 영상입니다. 기존 영상을 열어주세요.",
                                 "existing_id": str(duplicate.id)}, status=409)
            video = PublicVideo.objects.create(
                tenant=request.tenant, youtube_video_id=normalized["youtube_video_id"],
                canonical_url=normalized["canonical_url"], request_id=data["request_id"],
                draft_content=data["draft_content"], creation_content=data["draft_content"],
                created_by=request.user, updated_by=request.user,
                source_title=metadata["source_title"] if metadata else "",
                channel_title=metadata["channel_title"] if metadata else "",
                thumbnail_url=metadata["thumbnail_url"] if metadata else "",
                metadata_checked_at=timezone.now() if metadata else None,
            )
            record_event(request, "create", video)
            return Response(managed_video(video), status=201)


class ManageVideoDetailView(VideoAPIView):
    @extend_schema(operation_id="managed_videos_detail", responses=ManagedVideoSerializer)
    def get(self, request, pk):
        require_publisher(request)
        return Response(managed_video(get_object_or_404(self.videos(request), pk=pk)))

    @extend_schema(request=VideoEditSerializer, responses=ManagedVideoSerializer)
    def patch(self, request, pk):
        data = validated(VideoEditSerializer, request.data)
        with transaction.atomic():
            require_publisher(request, lock=True)
            video = get_object_or_404(self.videos(request).select_for_update(), pk=pk)
            check_revision(video.revision, data["expected_revision"])
            # Older open editors do not know the free-form label; preserve it.
            if "category_label" not in data["draft_content"] and "category_label" in video.draft_content:
                data["draft_content"]["category_label"] = video.draft_content["category_label"]
            if video.draft_content != data["draft_content"]:
                video.draft_content = data["draft_content"]
                video.updated_by = request.user
                video.revision += 1
                video.save(update_fields=["draft_content", "updated_by", "revision", "updated_at"])
                record_event(request, "edit", video)
            return Response(managed_video(video))

    @extend_schema(parameters=[VideoRevisionSerializer], responses={204: None})
    def delete(self, request, pk):
        data = validated(VideoRevisionSerializer, request.query_params)
        with transaction.atomic():
            require_publisher(request, lock=True)
            video = get_object_or_404(PublicVideo.objects.select_for_update(), tenant=request.tenant, pk=pk)
            # A lost response is safe to retry; the old create request stays a tombstone.
            if video.status == "deleted":
                return Response(status=204)
            showcase = showcase_for_write(request.tenant)
            check_revision(video.revision, data["expected_revision"])
            check_revision(showcase.revision, data["expected_showcase_revision"])
            if showcase.hero_video_id == video.id:
                update_hero(request, showcase, None)
            video.status = "deleted"
            video.updated_by = request.user
            video.revision += 1
            video.save(update_fields=["status", "updated_by", "revision", "updated_at"])
            record_event(request, "delete", video)
            return Response(status=204)


class ManageVideoPublishView(VideoAPIView):
    @extend_schema(request=VideoPublishSerializer, responses=VideoPublicationSerializer)
    def post(self, request, pk):
        require_publisher(request)
        data = validated(VideoPublishSerializer, request.data)
        current = get_object_or_404(self.videos(request), pk=pk)
        check_revision(current.revision, data["expected_revision"])
        metadata = resolve_youtube(current.canonical_url)
        with transaction.atomic():
            require_publisher(request, lock=True)
            showcase = showcase_for_write(request.tenant)
            video = get_object_or_404(self.videos(request).select_for_update(), pk=pk)
            check_revision(video.revision, data["expected_revision"])
            check_revision(showcase.revision, data["expected_showcase_revision"])
            video.published_content = validated(VideoContentSerializer, video.draft_content)
            video.status = "published"
            for key in ("source_title", "channel_title", "thumbnail_url"):
                setattr(video, key, metadata[key])
            video.metadata_checked_at = timezone.now()
            video.published_at = timezone.now()
            video.updated_by = request.user
            video.revision += 1
            video.save()
            if data["set_as_hero"]:
                update_hero(request, showcase, video)
            elif showcase.hero_video_id == video.id:
                update_hero(request, showcase, None)
            record_event(request, "publish", video)
        # Publication is committed before provider I/O. Public GET/HEAD never write.
        refresh_video_statistics(request.tenant, video_id=pk)
        return Response({"video": managed_video(video), "showcase_revision": showcase.revision})


class ManageVideoUnpublishView(VideoAPIView):
    @extend_schema(request=VideoPublishSerializer, responses=VideoPublicationSerializer)
    def post(self, request, pk):
        data = validated(VideoPublishSerializer, request.data)
        with transaction.atomic():
            require_publisher(request, lock=True)
            showcase = showcase_for_write(request.tenant)
            video = get_object_or_404(self.videos(request).select_for_update(), pk=pk)
            check_revision(video.revision, data["expected_revision"])
            check_revision(showcase.revision, data["expected_showcase_revision"])
            if video.status != "unpublished":
                video.status = "unpublished"
                video.updated_by = request.user
                video.revision += 1
                video.save(update_fields=["status", "updated_by", "revision", "updated_at"])
                record_event(request, "unpublish", video)
            if showcase.hero_video_id == video.id:
                update_hero(request, showcase, None)
            return Response({"video": managed_video(video), "showcase_revision": showcase.revision})


class ManageVideoShowcaseView(VideoAPIView):
    @extend_schema(responses=ManagedShowcaseSerializer)
    def get(self, request):
        require_publisher(request)
        row = PublicVideoShowcase.objects.filter(tenant=request.tenant).first()
        return Response(managed_showcase(row))

    @extend_schema(request=VideoShowcaseSerializer, responses=ManagedShowcaseSerializer)
    def patch(self, request):
        data = validated(VideoShowcaseSerializer, request.data)
        with transaction.atomic():
            require_publisher(request, lock=True)
            row = showcase_for_write(request.tenant)
            check_revision(row.revision, data["expected_revision"])
            if "hero_video_id" in data:
                video = get_object_or_404(self.published(request), pk=data["hero_video_id"]) if data["hero_video_id"] else None
                update_hero(request, row, video)
            changed = [field for field in ("title", "description") if field in data and getattr(row, field) != data[field]]
            if changed:
                for field in changed:
                    setattr(row, field, data[field])
                row.revision += 1
                row.save(update_fields=[*changed, "revision"])
                record_event(request, "settings", None, {"fields": changed, "showcase_revision": row.revision})
            return Response(managed_showcase(row))
