"""School report term entry and staff collection endpoints."""

from decimal import Decimal

from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.support.results.school_reports import (
    SchoolReportTerm,
    own_student,
    request_missing_scores,
    roster_entries,
    roster_page,
    roster_query,
    save_direct_scores,
    staff_student,
    term_detail,
)


class SchoolReportTermSerializer(serializers.Serializer):
    academic_year = serializers.IntegerField(min_value=2000, max_value=2100)
    semester = serializers.IntegerField(min_value=1, max_value=2)
    exam_round = serializers.ChoiceField(choices=("first", "second"))

    def term(self):
        return SchoolReportTerm(
            academic_year=self.validated_data["academic_year"],
            semester=self.validated_data["semester"],
            exam_round=self.validated_data["exam_round"],
        )


class SchoolReportRosterSerializer(SchoolReportTermSerializer):
    lecture_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)
    search = serializers.CharField(max_length=100, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=("submitted", "missing"), required=False)


class SchoolReportSubjectSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=50)
    score = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal("0"))
    max_score = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal("0.01"))
    grade_rank = serializers.IntegerField(min_value=1, max_value=9, required=False, allow_null=True)
    grade_scale = serializers.ChoiceField(choices=("five", "nine"), required=False, allow_blank=True, allow_null=True)
    achievement_level = serializers.ChoiceField(
        choices=("A", "B", "C", "D", "E"), required=False, allow_blank=True, allow_null=True,
    )
    subject_average = serializers.DecimalField(
        max_digits=7, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True,
    )
    standard_deviation = serializers.DecimalField(
        max_digits=7, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True,
    )
    cohort_size = serializers.IntegerField(min_value=1, max_value=100000, required=False, allow_null=True)
    exam_date = serializers.DateField(required=False, allow_null=True)


class SchoolReportEntrySerializer(serializers.Serializer):
    subjects = SchoolReportSubjectSerializer(many=True, allow_empty=False, max_length=20)


class SchoolReportRequestSerializer(SchoolReportTermSerializer):
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=2**63 - 1), min_length=1, max_length=100,
    )

    def validate_student_ids(self, value):
        return list(dict.fromkeys(value))


class SchoolReportPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


def _validated_term(data, serializer_class=SchoolReportTermSerializer):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer


class MySchoolReportView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        student = own_student(request.tenant, request.user)
        term = _validated_term(request.query_params).term()
        return Response(term_detail(request.tenant, student, term))

    def put(self, request):
        student = own_student(request.tenant, request.user)
        term = _validated_term(request.query_params).term()
        body = SchoolReportEntrySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(save_direct_scores(
            request.tenant, student, request.user, term, body.validated_data["subjects"],
        ))


class SchoolReportRosterView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        query = _validated_term(request.query_params, SchoolReportRosterSerializer)
        term = query.term()
        roster, counts = roster_query(request.tenant, term, query.validated_data.get("lecture_id"))
        roster = roster_page(
            request.tenant, term, roster, query.validated_data.get("search"), query.validated_data.get("status"),
        )
        paginator = SchoolReportPagination()
        page = paginator.paginate_queryset(roster, request, view=self)
        response = paginator.get_paginated_response(roster_entries(request.tenant, term, page))
        response.data.update(counts)
        return response


class SchoolReportDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, student_id):
        term = _validated_term(request.query_params).term()
        student = staff_student(request.tenant, student_id)
        return Response(term_detail(request.tenant, student, term))


class SchoolReportRequestView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        body = _validated_term(request.data, SchoolReportRequestSerializer)
        return Response(request_missing_scores(
            request.tenant, body.term(), body.validated_data["student_ids"],
        ))
