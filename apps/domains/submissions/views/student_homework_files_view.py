from collections import defaultdict

from django.db.models import Exists, OuterRef, Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission, SubmissionMedia
from apps.domains.submissions.services.homework_media import (
    homework_media_set_fingerprint_for_submissions,
    legacy_homework_media_removed_at,
    serialize_homework_media,
    serialize_legacy_homework_media,
)
from apps.support.submissions.dependencies import (
    staff_homework_file_targets,
    staff_submission_student_exists,
)


from apps.support.submissions.homework_acceptance import (
    homework_acceptance_payload,
    homework_acceptance_rows,
)


class StudentHomeworkFileSerializer(serializers.Serializer):
    id = serializers.CharField()
    legacy = serializers.BooleanField()
    client_file_id = serializers.UUIDField(allow_null=True)
    upload_batch_id = serializers.UUIDField(allow_null=True)
    position = serializers.IntegerField()
    original_filename = serializers.CharField()
    media_kind = serializers.ChoiceField(choices=SubmissionMedia.Kind.choices)
    mime_type = serializers.CharField()
    file_size = serializers.IntegerField()
    status = serializers.ChoiceField(choices=SubmissionMedia.Status.choices)
    error_message = serializers.CharField(allow_blank=True)
    upload_started_at = serializers.DateTimeField(allow_null=True)
    uploaded_at = serializers.DateTimeField(allow_null=True)
    failed_at = serializers.DateTimeField(allow_null=True)
    removed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)


class StudentHomeworkFileSubmissionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    homework_title = serializers.CharField()
    lecture_title = serializers.CharField()
    session_title = serializers.CharField()
    session_id = serializers.IntegerField()
    teacher_accepted = serializers.BooleanField()
    teacher_accepted_updated_at = serializers.DateTimeField(allow_null=True)
    media_set_fingerprint = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    files = StudentHomeworkFileSerializer(many=True)


class StudentHomeworkFilesPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = StudentHomeworkFileSubmissionSerializer(many=True)


class StudentHomeworkFilesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        parameters=[
            OpenApiParameter("page", int, OpenApiParameter.QUERY, default=1),
            OpenApiParameter("page_size", int, OpenApiParameter.QUERY, default=30),
        ],
        responses=StudentHomeworkFilesPageSerializer,
    )
    def get(self, request, student_id: int):
        tenant = request.tenant
        if not staff_submission_student_exists(tenant=tenant, student_id=student_id):
            raise NotFound("학생을 찾을 수 없습니다.")

        parse_query_int(request.query_params, "page", min_value=1, max_value=1_000_000, default=1)
        paginator = PageNumberPagination()
        paginator.page_size = parse_query_int(
            request.query_params, "page_size", min_value=1, max_value=100, default=30,
        )

        targets = staff_homework_file_targets(tenant=tenant)
        visible_media = SubmissionMedia.objects.filter(
            tenant=tenant, status=SubmissionMedia.Status.UPLOADED, removed_at__isnull=True,
        ).exclude(object_key__in=["", "pending"])
        legacy_file = (
            Q(file_key__isnull=False, meta__homework_media_legacy_removed_at__isnull=True)
            & ~Q(file_key__in=["", "pending"])
        )
        submissions = (
            Submission.objects.filter(
                tenant=tenant, target_type=Submission.TargetType.HOMEWORK,
                source__in=[Submission.Source.HOMEWORK_IMAGE, Submission.Source.HOMEWORK_VIDEO],
                enrollment__tenant=tenant, enrollment__student_id=student_id,
                enrollment__student__tenant=tenant, enrollment__student__deleted_at__isnull=True,
                enrollment__lecture__tenant=tenant,
            )
            .exclude(status__in=[Submission.Status.FAILED, Submission.Status.SUPERSEDED])
            .filter(Exists(targets.filter(
                pk=OuterRef("target_id"), session__lecture_id=OuterRef("enrollment__lecture_id"),
            )))
            .filter(Exists(visible_media.filter(submission_id=OuterRef("pk"))) | legacy_file)
            .prefetch_related(Prefetch("media_files", queryset=visible_media, to_attr="visible_media"))
            .order_by("-created_at", "-id")
        )
        page = paginator.paginate_queryset(submissions, request, view=self)
        homework_by_id = targets.select_related("session__lecture").in_bulk({row.target_id for row in page})

        enrollment_ids = {row.enrollment_id for row in page}
        acceptances = {
            (row.enrollment_id, row.homework_id): row
            for row in homework_acceptance_rows(tenant=tenant, enrollment_ids=enrollment_ids, homework_ids=homework_by_id)
        }
        evidence = defaultdict(list)
        for row in Submission.objects.filter(
            tenant=tenant, enrollment_id__in=enrollment_ids, target_id__in=homework_by_id,
            target_type=Submission.TargetType.HOMEWORK,
            source__in=[Submission.Source.HOMEWORK_IMAGE, Submission.Source.HOMEWORK_VIDEO],
        ).exclude(status=Submission.Status.SUPERSEDED).prefetch_related(
            Prefetch("media_files", queryset=SubmissionMedia.objects.filter(tenant=tenant))
        ):
            evidence[row.enrollment_id, row.target_id].append(row)
        fingerprints = {key: homework_media_set_fingerprint_for_submissions(rows) for key, rows in evidence.items()}

        results = []
        for submission in page:
            homework = homework_by_id.get(submission.target_id)
            key = (submission.enrollment_id, submission.target_id)
            if homework is None or key not in fingerprints:
                continue
            files = [serialize_homework_media(media) for media in submission.visible_media]
            if submission.file_key not in (None, "", "pending") and not legacy_homework_media_removed_at(submission):
                files.append(serialize_legacy_homework_media(submission))
            files.sort(key=lambda file: (file["position"], file["created_at"] or "", file["id"]))
            if files:
                results.append({
                    "id": submission.pk,
                    "homework_id": homework.pk,
                    "enrollment_id": submission.enrollment_id,
                    "homework_title": homework.title,
                    "lecture_title": homework.session.lecture.title,
                    "session_title": homework.session.title,
                    "session_id": homework.session_id,
                    **homework_acceptance_payload(acceptances.get(key)),
                    "media_set_fingerprint": fingerprints[key],
                    "submitted_at": submission.created_at.isoformat(),
                    "files": files,
                })
        return paginator.get_paginated_response(results)
