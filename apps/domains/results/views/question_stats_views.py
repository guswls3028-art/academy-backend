# apps/domains/results/views/question_stats_views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.api.common.query_params import parse_query_int
from apps.domains.results.services.question_stats_service import QuestionStatsService
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.serializers.question_stats import (
    QuestionStatSerializer,
    TopWrongQuestionSerializer,
)
from apps.support.results.admin_exam_dependencies import regular_active_exam_with_session_exists


def _verify_exam_tenant(request, exam_id: int) -> None:
    """✅ tenant isolation: verify exam belongs to request.tenant"""
    from rest_framework.exceptions import NotFound
    if not regular_active_exam_with_session_exists(exam_id=int(exam_id), tenant=request.tenant):
        raise NotFound("Exam not found for this tenant.")


def _finalized_representative_scope(*, exam_id: int, tenant) -> tuple[list[int], list[int]]:
    """Return current finalized attempt ids plus tenant-scoped legacy enrollments."""
    results = (
        latest_results_per_enrollment(target_type="exam", target_id=int(exam_id))
        .filter(enrollment__tenant=tenant)
        .exclude(enrollment_id__isnull=True)
        .select_related("attempt")
    )
    attempt_ids: list[int] = []
    legacy_enrollment_ids: list[int] = []
    for result in results:
        attempt = result.attempt
        if attempt is None:
            legacy_enrollment_ids.append(int(result.enrollment_id))
            continue
        meta = attempt.meta if isinstance(attempt.meta, dict) else {}
        if meta.get("status") == "NOT_SUBMITTED" or attempt.status != "done":
            continue
        attempt_ids.append(int(attempt.id))
    return attempt_ids, legacy_enrollment_ids


class AdminExamQuestionStatsView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/

    ✅ 단일 진실:
    - ResultFact 기반 (append-only)
    - 대표 attempt 교체/재시험 여부와 무관하게 항상 일관된 통계
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        _verify_exam_tenant(request, int(exam_id))
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
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
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
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
        n = min(
            parse_query_int(request.query_params, "n", default=5, min_value=1),
            100,
        )
        attempt_ids, legacy_enrollment_ids = _finalized_representative_scope(
            exam_id=int(exam_id),
            tenant=request.tenant,
        )
        data = QuestionStatsService.top_n_wrong_questions(
            exam_id=int(exam_id),
            n=n,
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        )
        return Response(TopWrongQuestionSerializer(data, many=True).data)
