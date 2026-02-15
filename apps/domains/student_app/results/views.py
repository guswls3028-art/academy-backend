# apps/domains/student_app/results/views.py
"""
GET /student/results/me/exams/<exam_id>/ 및 /items/
→ results 도메인 단일 진실(get_my_exam_result_data) 사용.
"""
from django.http import Http404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent
from apps.domains.results.services.student_result_service import get_my_exam_result_data


class MyExamResultView(APIView):
    """
    GET /student/results/me/exams/{exam_id}/
    결과 도메인 Result 기준 실제 채점 데이터 반환.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, exam_id):
        try:
            data = get_my_exam_result_data(request, int(exam_id))
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
            data = get_my_exam_result_data(request, int(exam_id))
        except Http404:
            return Response({"detail": "result not found"}, status=404)
        return Response({"items": data.get("items") or []})
