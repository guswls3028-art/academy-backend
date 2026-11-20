"""Parent-only feedback API, separate from student results and attendance notes."""
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.serializers.assessment_feedback import (
    AssessmentDetailErrorSerializer,
    AdminParentCommentListResponseSerializer,
    AdminParentCommentResponseSerializer,
    ParentCommentConflictSerializer,
    ParentAssessmentCommentsResponseSerializer,
)

ASSESSMENT_KIND_PARAMETER = OpenApiParameter(
    name="kind", location=OpenApiParameter.PATH, required=True,
    type=OpenApiTypes.STR, enum=["exam", "homework"],
    description="The exact session-linked assessment type.",
)

from apps.support.results.parent_comment_dependencies import (
    get_request_student, is_request_parent,
    parent_comment_titles,
)
from apps.domains.results.models.parent_assessment_comment import (
    ParentAssessmentComment, ParentAssessmentCommentRevision,
)
from apps.domains.results.services.assessment_scope import assessment_scope


class CommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)
    version = serializers.IntegerField(min_value=0)


def payload(comment, enrollment_id, *, history=False):
    data = {"enrollment_id": enrollment_id, "text": comment.text if comment else "",
            "version": comment.version if comment else 0,
            "updated_at": comment.updated_at if comment else None,
            "updated_by": comment.updated_by_id if comment else None}
    if history:
        revisions = getattr(comment, "loaded_revisions", None) if comment else []
        if revisions is None:
            revisions = comment.revisions.select_related("author").all()
        data["history"] = [{"version": r.version, "text": r.text, "author_id": r.author_id,
                            "author_name": (getattr(r.author, "name", "") or r.author.get_full_name() or "직원") if r.author else "삭제된 계정",
                            "created_at": r.created_at}
                           for r in revisions]
    return data


class AdminParentAssessmentCommentsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="results_admin_parent_assessment_comments_list",
        parameters=[ASSESSMENT_KIND_PARAMETER],
        responses={200: AdminParentCommentListResponseSerializer, 403: AssessmentDetailErrorSerializer, 404: AssessmentDetailErrorSerializer},
        description="Staff-only assigned student rows and explicit parent-facing comment edit history, scoped to the request tenant and live session assessment.",
    )
    def get(self, request, session_id, kind, assessment_id):
        _, enrollments = assessment_scope(request.tenant, session_id, kind, assessment_id)
        comments = {c.enrollment_id: c for c in ParentAssessmentComment.objects.filter(
            tenant=request.tenant, session_id=session_id, assessment_kind=kind,
            assessment_id=assessment_id, enrollment__in=enrollments).prefetch_related(Prefetch(
                "revisions", queryset=ParentAssessmentCommentRevision.objects.select_related("author"),
                to_attr="loaded_revisions",
            ))}
        return Response({"items": [dict(payload(comments.get(e.id), e.id, history=True),
                                        student_id=e.student_id, student_name=e.student.name)
                                   for e in enrollments.select_related("student").order_by("student__name", "id")]})


class AdminParentAssessmentCommentView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="results_admin_parent_assessment_comment_update",
        parameters=[ASSESSMENT_KIND_PARAMETER],
        request=CommentWriteSerializer,
        responses={200: AdminParentCommentResponseSerializer, 400: OpenApiTypes.OBJECT, 403: AssessmentDetailErrorSerializer, 404: AssessmentDetailErrorSerializer, 409: ParentCommentConflictSerializer},
        description="Atomically save parent-facing text and a revision. Version 0 creates the first comment; a stale version returns current state with 409. Blank/whitespace-only text clears publication without deleting history.",
    )
    @transaction.atomic
    def put(self, request, session_id, kind, assessment_id, enrollment_id):
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _, enrollments = assessment_scope(request.tenant, session_id, kind, assessment_id)
        # Enrollment lock also serializes first insert, including concurrent empty writes.
        get_object_or_404(enrollments.select_for_update(), id=enrollment_id)
        comment, _ = ParentAssessmentComment.objects.get_or_create(
            tenant=request.tenant, session_id=session_id, enrollment_id=enrollment_id,
            assessment_kind=kind, assessment_id=assessment_id)
        if comment.version != serializer.validated_data["version"]:
            return Response({"detail": "다른 선생님이 수정했습니다. 새로고침 후 다시 저장해 주세요.",
                             "current": payload(comment, enrollment_id)}, status=409)
        comment.text = serializer.validated_data["text"]
        if not comment.text.strip():
            comment.text = ""
        comment.version += 1
        comment.updated_by = request.user
        comment.save(update_fields=["text", "version", "updated_by", "updated_at"])
        ParentAssessmentCommentRevision.objects.create(comment=comment, version=comment.version,
                                                       text=comment.text, author=request.user)
        return Response(payload(comment, enrollment_id, history=True))


class ParentAssessmentCommentsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_parent_assessment_comments_list",
        parameters=[OpenApiParameter(name="X-Student-Id", location=OpenApiParameter.HEADER, required=True, type=OpenApiTypes.INT, description="Explicitly selected active child owned by the authenticated parent in the request tenant.")],
        responses={200: ParentAssessmentCommentsResponseSerializer, 403: AssessmentDetailErrorSerializer},
        description="Parent-only read projection for the selected child, including unscored assessment titles. Student accounts are denied. Internal memos and edit history are never included.",
    )
    def get(self, request):
        if not is_request_parent(request):
            raise PermissionDenied("학부모 전용 정보입니다.")
        student = get_request_student(request)
        if student is None:
            raise PermissionDenied("자녀를 선택해 주세요.")
        comments = list(ParentAssessmentComment.objects.filter(
            tenant=request.tenant, enrollment__tenant=request.tenant, enrollment__student=student,
            enrollment__student__tenant=request.tenant,
        ).exclude(text="").select_related("session__lecture").order_by("session_id", "assessment_kind", "assessment_id"))
        titles = parent_comment_titles(tenant=request.tenant, student=student, comments=comments)
        items = []
        # Revalidate live assignment before publishing any stored feedback.
        for comment in comments:
            if comment.pk not in titles:
                continue
            session = comment.session
            items.append({"session_id": comment.session_id, "session_title": session.title,
                          "lecture_title": session.lecture.title, "assessment_title": titles[comment.pk],
                          "assessment_kind": comment.assessment_kind,
                          "assessment_id": comment.assessment_id, "text": comment.text,
                          "version": comment.version, "updated_at": comment.updated_at})
        return Response({"items": items})
