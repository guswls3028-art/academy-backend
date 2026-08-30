# apps/domains/results/views/question_stats_views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.api.common.query_params import parse_query_int
from apps.domains.results.services.question_stats_service import QuestionStatsService
from apps.domains.results.models import ExamAttempt
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.serializers.question_stats import (
    QuestionStatSerializer,
    TopWrongQuestionSerializer,
)
from apps.support.results.admin_exam_dependencies import regular_active_exam_with_session_exists
from apps.support.results.exam_policy_dependencies import (
    exam_has_linked_lecture,
    linked_exam_lecture_ids,
)


def _verify_exam_tenant(request, exam_id: int) -> None:
    """✅ tenant isolation: verify exam belongs to request.tenant"""
    from rest_framework.exceptions import NotFound
    if not regular_active_exam_with_session_exists(exam_id=int(exam_id), tenant=request.tenant):
        raise NotFound("Exam not found for this tenant.")


def _selected_lecture_id(request, *, exam_id: int) -> int | None:
    lecture_id = parse_query_int(
        request.query_params,
        "lecture_id",
        min_value=1,
    )
    if lecture_id is None:
        return None
    if not exam_has_linked_lecture(
        exam_id=int(exam_id),
        lecture_id=lecture_id,
        tenant=request.tenant,
    ):
        raise ValidationError(
            {"lecture_id": "이 시험에 연결되지 않은 강의입니다."}
        )
    return lecture_id


def _linked_lecture_ids(request, *, exam_id: int) -> set[int]:
    return linked_exam_lecture_ids(
        exam_id=int(exam_id),
        tenant=request.tenant,
    )


def _finalized_representative_scope(
    *,
    exam_id: int,
    tenant,
    lecture_id: int | None = None,
    lecture_ids: set[int] | None = None,
) -> tuple[list[int], list[int]]:
    """Return finalized first-attempt ids plus tenant-scoped no-attempt legacy rows."""
    result_queryset = (
        latest_results_per_enrollment(target_type="exam", target_id=int(exam_id))
        .filter(enrollment__tenant=tenant)
        .exclude(enrollment_id__isnull=True)
        .select_related("attempt")
    )
    if lecture_id is not None:
        result_queryset = result_queryset.filter(
            enrollment__lecture_id=int(lecture_id)
        )
    elif lecture_ids is not None:
        result_queryset = result_queryset.filter(
            enrollment__lecture_id__in=lecture_ids
        )
    results = list(result_queryset)
    enrollment_ids = [int(result.enrollment_id) for result in results]
    first_attempts = list(
        ExamAttempt.objects.filter(
            exam_id=int(exam_id),
            enrollment_id__in=enrollment_ids,
            attempt_index=1,
        ).only("id", "enrollment_id", "status", "meta")
    )
    first_enrollment_ids = {int(attempt.enrollment_id) for attempt in first_attempts}
    attempt_ids = [
        int(attempt.id)
        for attempt in first_attempts
        if attempt.status == "done"
        and not (
            isinstance(attempt.meta, dict)
            and attempt.meta.get("status") == "NOT_SUBMITTED"
        )
    ]
    legacy_enrollment_ids = [
        int(result.enrollment_id)
        for result in results
        if result.attempt_id is None
        and int(result.enrollment_id) not in first_enrollment_ids
    ]
    return attempt_ids, legacy_enrollment_ids


class AdminExamQuestionStatsView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/

    ✅ 단일 진실:
    - ResultFact 기반 (append-only)
    - 대표 attempt 교체/재시험 여부와 무관하게 1차 문항 통계
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        _verify_exam_tenant(request, int(exam_id))
        lecture_id = _selected_lecture_id(request, exam_id=int(exam_id))
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
            lecture_id=lecture_id,
            lecture_ids=(
                None
                if lecture_id is not None
                else _linked_lecture_ids(request, exam_id=int(exam_id))
            ),
        )
        data = QuestionStatsService.per_question_stats(
            exam_id=int(exam_id),
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        )
        return Response(QuestionStatSerializer(data, many=True).data)


class ExamQuestionWrongDistributionView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/{question_id}/wrong-distribution/
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, question_id: int):
        _verify_exam_tenant(request, int(exam_id))
        lecture_id = _selected_lecture_id(request, exam_id=int(exam_id))
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
            lecture_id=lecture_id,
            lecture_ids=(
                None
                if lecture_id is not None
                else _linked_lecture_ids(request, exam_id=int(exam_id))
            ),
        )
        dist = QuestionStatsService.wrong_choice_distribution(
            exam_id=int(exam_id),
            question_id=int(question_id),
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        )
        return Response(
            {
                "question_id": int(question_id),
                "distribution": dist,
            }
        )


class ExamTopWrongQuestionsView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/top-wrong/?n=5
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        _verify_exam_tenant(request, int(exam_id))
        lecture_id = _selected_lecture_id(request, exam_id=int(exam_id))
        n = min(
            parse_query_int(request.query_params, "n", default=5, min_value=1),
            100,
        )
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
            lecture_id=lecture_id,
            lecture_ids=(
                None
                if lecture_id is not None
                else _linked_lecture_ids(request, exam_id=int(exam_id))
            ),
        )
        data = QuestionStatsService.top_n_wrong_questions(
            exam_id=int(exam_id),
            n=n,
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        )
        return Response(TopWrongQuestionSerializer(data, many=True).data)
