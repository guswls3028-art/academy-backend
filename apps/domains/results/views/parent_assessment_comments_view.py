"""Parent-only feedback API, separate from student results and attendance notes."""
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.results.parent_comment_dependencies import (
    Enrollment, SessionEnrollment, ExamEnrollment, HomeworkAssignment, Homework, Session,
    get_request_student, is_request_parent, learning_history_enrollments_for_student,
    session_score_enrollment_ids, get_exam_target_enrollment_pairs_for_session,
)
from apps.domains.results.models.parent_assessment_comment import (
    ParentAssessmentComment, ParentAssessmentCommentRevision,
)
from apps.domains.results.utils.session_exam import get_exams_for_session


class CommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)
    version = serializers.IntegerField(min_value=0)


def assessment_scope(tenant, session_id, kind, assessment_id, *, student=None):
    if tenant is None:
        raise PermissionDenied("Tenant required.")
    session = get_object_or_404(Session, id=session_id, lecture__tenant=tenant)
    if kind == "exam":
        get_object_or_404(get_exams_for_session(session), id=assessment_id, tenant=tenant)
    elif kind == "homework":
        get_object_or_404(Homework.objects.exclude(meta__removed_from_session_at__isnull=False),
                          id=assessment_id, tenant=tenant, session=session, homework_type="regular")
    else:
        raise NotFound()
    enrollments = Enrollment.objects.filter(
        tenant=tenant, lecture_id=session.lecture_id, status="ACTIVE",
        student__tenant=tenant, student__deleted_at__isnull=True,
        id__in=session_score_enrollment_ids(tenant=tenant, session=session),
    )
    if student is not None:
        enrollments = enrollments.filter(id__in=learning_history_enrollments_for_student(tenant=tenant, student=student).values("id"))
    if kind == "exam":
        pairs = get_exam_target_enrollment_pairs_for_session(session=session, enrollment_ids=list(enrollments.values_list("id", flat=True)))
        enrollments = enrollments.filter(id__in=[eid for eid, exam_id in pairs if exam_id == assessment_id])
    else:
        enrollments = enrollments.filter(id__in=HomeworkAssignment.objects.filter(
            tenant=tenant, session=session, homework_id=assessment_id).values("enrollment_id"))
    return session, enrollments


def payload(comment, enrollment_id, *, history=False):
    data = {"enrollment_id": enrollment_id, "text": comment.text if comment else "",
            "version": comment.version if comment else 0,
            "updated_at": comment.updated_at if comment else None,
            "updated_by": comment.updated_by_id if comment else None}
    if history:
        data["history"] = [{"version": r.version, "text": r.text, "author_id": r.author_id,
                            "author_name": (getattr(r.author, "name", "") or r.author.get_full_name() or "직원") if r.author else "삭제된 계정",
                            "created_at": r.created_at}
                           for r in comment.revisions.select_related("author").all()] if comment else []
    return data


class AdminParentAssessmentCommentsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id, kind, assessment_id):
        _, enrollments = assessment_scope(request.tenant, session_id, kind, assessment_id)
        comments = {c.enrollment_id: c for c in ParentAssessmentComment.objects.filter(
            tenant=request.tenant, session_id=session_id, assessment_kind=kind,
            assessment_id=assessment_id, enrollment__in=enrollments).prefetch_related("revisions")}
        return Response({"items": [dict(payload(comments.get(e.id), e.id, history=True),
                                        student_id=e.student_id, student_name=e.student.name)
                                   for e in enrollments.select_related("student").order_by("student__name", "id")]})


class AdminParentAssessmentCommentView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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

    def get(self, request):
        if not is_request_parent(request):
            raise PermissionDenied("학부모 전용 정보입니다.")
        student = get_request_student(request)
        if student is None:
            raise PermissionDenied("자녀를 선택해 주세요.")
        comments = ParentAssessmentComment.objects.filter(
            tenant=request.tenant, enrollment__tenant=request.tenant, enrollment__student=student, enrollment__student__tenant=request.tenant).exclude(text="")
        items = []
        # Revalidate live assignment before publishing any stored feedback.
        for comment in comments.order_by("session_id", "assessment_kind", "assessment_id"):
            try:
                session, enrollments = assessment_scope(request.tenant, comment.session_id,
                                                  comment.assessment_kind, comment.assessment_id, student=student)
            except (NotFound, Http404):
                continue
            if not enrollments.filter(id=comment.enrollment_id).exists():
                continue
            assessment_title = (get_exams_for_session(session).filter(id=comment.assessment_id).values_list("title", flat=True).first()
                                if comment.assessment_kind == "exam" else
                                Homework.objects.filter(tenant=request.tenant, id=comment.assessment_id).values_list("title", flat=True).first())
            items.append({"session_id": comment.session_id, "session_title": session.title,
                          "lecture_title": session.lecture.title, "assessment_title": assessment_title,
                          "assessment_kind": comment.assessment_kind,
                          "assessment_id": comment.assessment_id, "text": comment.text,
                          "version": comment.version, "updated_at": comment.updated_at})
        return Response({"items": items})
