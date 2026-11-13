"""School report term entry and staff collection endpoints."""

from decimal import Decimal

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.support.messaging.school_report_requests import school_report_alimtalk_available
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


@extend_schema_field({"oneOf": [{"type": "number"}, {"type": "string"}]})
class SchoolReportDecimalField(serializers.DecimalField):
    """Score inputs accept JSON numbers and decimal strings."""


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
    score = SchoolReportDecimalField(max_digits=7, decimal_places=2, min_value=Decimal("0"))
    max_score = SchoolReportDecimalField(max_digits=7, decimal_places=2, min_value=Decimal("0.01"))
    grade_rank = serializers.IntegerField(min_value=1, max_value=9, required=False, allow_null=True)
    grade_scale = serializers.ChoiceField(choices=("five", "nine"), required=False, allow_blank=True, allow_null=True)
    achievement_level = serializers.ChoiceField(
        choices=("A", "B", "C", "D", "E"), required=False, allow_blank=True, allow_null=True,
    )
    subject_average = SchoolReportDecimalField(
        max_digits=7, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True,
    )
    standard_deviation = SchoolReportDecimalField(
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


class SchoolReportScoreResponseSerializer(SchoolReportTermSerializer):
    id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    source = serializers.ChoiceField(choices=("school_exam",))
    source_group = serializers.ChoiceField(choices=("school",))
    label = serializers.CharField()
    exam_name = serializers.CharField(allow_null=True)
    exam_month = serializers.IntegerField(allow_null=True)
    exam_date = serializers.DateField(allow_null=True)
    subject = serializers.CharField()
    score = serializers.FloatField()
    max_score = serializers.FloatField()
    score_pct = serializers.FloatField(allow_null=True)
    standard_score = serializers.FloatField(allow_null=True)
    percentile = serializers.FloatField(allow_null=True)
    grade_rank = serializers.IntegerField(allow_null=True)
    grade_scale = serializers.ChoiceField(choices=("five", "nine"), allow_null=True)
    achievement_level = serializers.ChoiceField(choices=("A", "B", "C", "D", "E"), allow_null=True)
    subject_average = serializers.FloatField(allow_null=True)
    standard_deviation = serializers.FloatField(allow_null=True)
    cohort_size = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=("pending", "verified"))
    review_note = serializers.CharField(allow_blank=True)
    direct_entry = serializers.BooleanField()
    evidence_file_id = serializers.IntegerField(allow_null=True)
    evidence_r2_key = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    reviewed_at = serializers.DateTimeField(allow_null=True)


class SchoolReportDetailResponseSerializer(SchoolReportTermSerializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    school = serializers.CharField(allow_blank=True)
    grade = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=("submitted", "missing"))
    submitted_at = serializers.DateTimeField(allow_null=True)
    requested_at = serializers.DateTimeField(allow_null=True)
    scores = SchoolReportScoreResponseSerializer(many=True)
    direct_scores = SchoolReportScoreResponseSerializer(many=True)


class SchoolReportRosterStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    name = serializers.CharField()
    school = serializers.CharField(allow_blank=True)
    grade = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=("submitted", "missing"))
    score_count = serializers.IntegerField(min_value=0)
    submitted_at = serializers.DateTimeField(allow_null=True)
    requested_at = serializers.DateTimeField(allow_null=True)
    can_request = serializers.BooleanField()
    can_request_alimtalk = serializers.BooleanField()


class SchoolReportRosterResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = SchoolReportRosterStudentSerializer(many=True)
    total_count = serializers.IntegerField(min_value=0)
    submitted_count = serializers.IntegerField(min_value=0)
    missing_count = serializers.IntegerField(min_value=0)
    alimtalk_available = serializers.BooleanField()


class SchoolReportRequestResponseSerializer(serializers.Serializer):
    requested = serializers.IntegerField(min_value=0)
    skipped = serializers.IntegerField(min_value=0)


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

    @extend_schema(
        operation_id="school_report_student_retrieve",
        parameters=[SchoolReportTermSerializer],
        responses=SchoolReportDetailResponseSerializer,
    )
    def get(self, request):
        student = own_student(request.tenant, request.user)
        term = _validated_term(request.query_params).term()
        return Response(term_detail(request.tenant, student, term))

    @extend_schema(
        operation_id="school_report_student_update",
        parameters=[SchoolReportTermSerializer],
        request=SchoolReportEntrySerializer,
        responses=SchoolReportDetailResponseSerializer,
    )
    def put(self, request):
        if getattr(request, "student_support_session", None) is not None:
            raise PermissionDenied("학생 본인만 학교 성적을 입력할 수 있습니다.")
        student = own_student(request.tenant, request.user)
        term = _validated_term(request.query_params).term()
        body = SchoolReportEntrySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(save_direct_scores(
            request.tenant, student, request.user, term, body.validated_data["subjects"],
        ))


class SchoolReportRosterView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="school_report_roster_list",
        parameters=[
            SchoolReportRosterSerializer,
            OpenApiParameter("page", type={"type": "integer", "minimum": 1}, description="Page number. Defaults to 1."),
            OpenApiParameter(
                "page_size", type={"type": "integer", "minimum": 1, "maximum": 100},
                description="Students per page. Defaults to 50.",
            ),
        ],
        responses=SchoolReportRosterResponseSerializer,
    )
    def get(self, request):
        query = _validated_term(request.query_params, SchoolReportRosterSerializer)
        term = query.term()
        roster, counts = roster_query(request.tenant, term, query.validated_data.get("lecture_id"))
        roster = roster_page(
            request.tenant, term, roster, query.validated_data.get("search"), query.validated_data.get("status"),
        )
        paginator = SchoolReportPagination()
        page = paginator.paginate_queryset(roster, request, view=self)
        alimtalk_available = school_report_alimtalk_available(request, request.tenant)
        response = paginator.get_paginated_response(roster_entries(
            request.tenant, term, page, alimtalk_available=alimtalk_available,
        ))
        response.data.update(counts)
        response.data["alimtalk_available"] = alimtalk_available
        return response


class SchoolReportDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="school_report_staff_retrieve",
        parameters=[SchoolReportTermSerializer],
        responses=SchoolReportDetailResponseSerializer,
    )
    def get(self, request, student_id):
        term = _validated_term(request.query_params).term()
        student = staff_student(request.tenant, student_id)
        return Response(term_detail(request.tenant, student, term))


class SchoolReportRequestView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="school_report_request_create",
        request=SchoolReportRequestSerializer,
        responses=SchoolReportRequestResponseSerializer,
    )
    def post(self, request):
        body = _validated_term(request.data, SchoolReportRequestSerializer)
        return Response(request_missing_scores(
            request.tenant, body.term(), body.validated_data["student_ids"],
        ))
