# apps/domains/student_app/results/views.py
"""
GET /student/results/me/exams/<exam_id>/ 및 /items/
→ results 도메인 단일 진실(get_my_exam_result_data) 사용.

GET /student/grades/
→ 학생 본인 시험 결과 목록 + 과제 성적 목록 (기입된 성적만).
"""
from django.http import Http404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.support.results.enterprise_analytics import (
    build_student_enterprise_analytics,
    normalize_analytics_days,
)
from apps.support.student_app.results_summary import (
    build_student_grades_summary,
    get_student_exam_result_data,
)


class MyExamResultView(APIView):
    """
    GET /student/results/me/exams/{exam_id}/
    결과 도메인 Result 기준 실제 채점 데이터 반환.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, exam_id):
        try:
            data = get_student_exam_result_data(request, int(exam_id), tenant=request.tenant)
        except Http404:
            return Response({"detail": "result not found"}, status=404)
        return Response(data)


class MyExamResultItemsView(APIView):
    """
    GET /student/results/me/exams/{exam_id}/items/
    동일 데이터의 items 배열만 반환 (프론트 문항별 결과 조회).
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, exam_id):
        try:
            data = get_student_exam_result_data(request, int(exam_id), tenant=request.tenant)
        except Http404:
            return Response({"detail": "result not found"}, status=404)
        return Response({"items": data.get("items") or []})


class MyGradesSummaryView(APIView):
    """
    GET /student/grades/
    학생 본인에 대해 기입된 시험 결과 목록 + 과제 성적 목록 반환.
    학생앱 성적 탭에서 시험 결과/과제 이력 카드에 사용.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "student not found"}, status=403)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"exams": [], "homeworks": []})
        return Response(build_student_grades_summary(tenant=tenant, student=student))


class MyGradesAnalyticsView(APIView):
    """
    GET /student/grades/analytics/
    학생 본인 또는 학부모가 선택한 자녀의 성적 추이/약점/과제 분석 반환.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "student not found"}, status=403)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant not resolved"}, status=403)

        days = normalize_analytics_days(request.query_params.get("days"), default=365)
        return Response(build_student_enterprise_analytics(tenant=tenant, student=student, days=days))
