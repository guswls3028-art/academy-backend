import csv
import io

from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from drf_spectacular.types import OpenApiTypes

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import is_effective_staff
from apps.support.surveys import active_student, resolve_response_student, selected_student_names, serialize_response_materials, student_choices
from .models import Survey, SurveyRecipient, SurveyResponse
from .serializers import (
    StudentSurveyDetailSerializer, SurveyActionSerializer, SurveyHistoryPageSerializer,
    SurveyPageSerializer, SurveyReadSerializer, SurveyRecipientsPageSerializer,
    SurveyResponseReadSerializer, SurveyResponseWriteSerializer, SurveyWriteSerializer,
)
from .services import SurveyConflict, close_survey, publish_survey, response_unavailable_reason, save_response, update_survey


class IsSurveyStaff(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request, "tenant", None) and is_effective_staff(request.user, request.tenant))


@extend_schema(tags=["Surveys"], responses=OpenApiTypes.OBJECT, request=None)
class StaffSurveyView(APIView):
    permission_classes = [IsAuthenticated, IsSurveyStaff]


@extend_schema(tags=["Student surveys"], responses=OpenApiTypes.OBJECT, request=None,
               parameters=[OpenApiParameter("X-Student-Id", int, OpenApiParameter.HEADER, description="Required for a linked parent; identifies the selected child.")])
class StudentSurveyView(APIView):
    permission_classes = [IsAuthenticated]

    def student(self, request):
        student = resolve_response_student(request)
        if not student:
            raise PermissionDenied("학생 또는 연결된 자녀 계정으로 이용해 주세요.")
        return student


def pagination(request):
    offset = parse_query_int(request.query_params, "offset", default=0, min_value=0, max_value=100000)
    limit = parse_query_int(request.query_params, "limit", default=30, min_value=1, max_value=100)
    return offset, limit


def with_counts(queryset, tenant):
    recipients = SurveyRecipient.objects.filter(tenant=tenant, survey_id=OuterRef("pk")).values("survey_id").annotate(total=Count("id")).values("total")
    responses = SurveyResponse.objects.filter(tenant=tenant, survey_id=OuterRef("pk")).values("survey_id").annotate(total=Count("id")).values("total")
    return queryset.annotate(target_count=Coalesce(Subquery(recipients, output_field=IntegerField()), Value(0)), response_count=Coalesce(Subquery(responses, output_field=IntegerField()), Value(0)))


def survey_data(survey, *, staff=False, has_response=False):
    count = getattr(survey, "response_count", None)
    if count is None:
        count = SurveyResponse.objects.filter(tenant_id=survey.tenant_id, survey=survey).count()
    result = {key: getattr(survey, key) for key in ["id", "title", "description", "kind", "status", "questions", "score_defaults", "allow_attachments", "opens_at", "closes_at", "response_limit", "published_at", "created_at", "updated_at", "version"]}
    reason = response_unavailable_reason(survey, count, has_response)
    result.update(response_count=count, response_unavailable_reason=reason, can_respond=not reason, answered=has_response)
    if staff:
        target_count = getattr(survey, "target_count", None)
        if target_count is None:
            target_count = SurveyRecipient.objects.filter(tenant_id=survey.tenant_id, survey=survey).count()
        result.update(audience=survey.audience, target_count=target_count)
    return result


def response_queryset(tenant):
    return SurveyResponse.objects.filter(tenant=tenant, survey__tenant=tenant, student__tenant=tenant).select_related("student", "survey").prefetch_related("attachments", "score_submissions")


def response_data(response):
    result = {"id": response.id, "survey_id": response.survey_id, "survey_title": response.survey.title, "kind": response.survey.kind, "questions": response.survey.questions, "student_id": response.student_id, "student_name": response.student.name, "student_ps": response.student.ps_number, "answers": response.answers, "version": response.version, "created_at": response.created_at, "updated_at": response.updated_at}
    result.update(serialize_response_materials(response))
    return result


def staff_survey(request, survey_id):
    survey = with_counts(Survey.objects.filter(tenant=request.tenant, id=survey_id), request.tenant).first()
    if not survey:
        raise NotFound()
    return survey


@extend_schema_view(
    get=extend_schema(operation_id="surveys_list", responses=SurveyPageSerializer, parameters=[OpenApiParameter("offset", int), OpenApiParameter("limit", int), OpenApiParameter("search", str), OpenApiParameter("status", str, enum=Survey.Status.values)]),
    post=extend_schema(request=SurveyWriteSerializer, responses={200: SurveyReadSerializer, 201: SurveyReadSerializer}),
)
class SurveyListView(StaffSurveyView):
    def get(self, request):
        offset, limit = pagination(request)
        rows = Survey.objects.filter(tenant=request.tenant)
        status = request.query_params.get("status")
        if status:
            if status not in Survey.Status.values:
                raise ValidationError("설문 상태를 확인해 주세요.")
            rows = rows.filter(status=status)
        search = request.query_params.get("search", "").strip()[:100]
        if search:
            rows = rows.filter(title__icontains=search)
        count = rows.count()
        return Response({"count": count, "results": [survey_data(row, staff=True) for row in with_counts(rows, request.tenant)[offset:offset + limit]]})

    def post(self, request):
        serializer = SurveyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("expected_version", None)
        creation_key = values.pop("creation_key", None)
        if creation_key:
            survey, created = Survey.objects.get_or_create(tenant=request.tenant, creation_key=creation_key, defaults={**values, "created_by": request.user})
            if not created and any(getattr(survey, key) != value for key, value in values.items()):
                raise SurveyConflict("이 요청으로 저장된 설문이 있습니다. 설문 목록에서 저장 내용을 확인해 주세요.")
        else:
            survey = Survey.objects.create(tenant=request.tenant, created_by=request.user, **values)
            created = True
        return Response(survey_data(survey, staff=True), status=201 if created else 200)


@extend_schema_view(get=extend_schema(responses=SurveyReadSerializer), patch=extend_schema(request=SurveyWriteSerializer, responses=SurveyReadSerializer))
class SurveyDetailView(StaffSurveyView):
    def get(self, request, survey_id):
        survey = staff_survey(request, survey_id)
        data = survey_data(survey, staff=True)
        data["selected_students"] = selected_student_names(tenant=request.tenant, student_ids=survey.audience.get("student_ids", [])) if survey.audience.get("mode") == "selected" else []
        return Response(data)

    def patch(self, request, survey_id):
        survey = staff_survey(request, survey_id)
        serializer = SurveyWriteSerializer(survey, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = update_survey(tenant=request.tenant, survey_id=survey_id, values=dict(serializer.validated_data))
        return Response(survey_data(saved, staff=True))


@extend_schema_view(post=extend_schema(request=SurveyActionSerializer, responses=SurveyReadSerializer))
class SurveyPublishView(StaffSurveyView):
    def post(self, request, survey_id):
        serializer = SurveyActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survey = publish_survey(tenant=request.tenant, survey_id=survey_id, expected_version=serializer.validated_data["expected_version"])
        return Response(survey_data(survey, staff=True))


@extend_schema_view(post=extend_schema(request=SurveyActionSerializer, responses=SurveyReadSerializer))
class SurveyCloseView(StaffSurveyView):
    def post(self, request, survey_id):
        serializer = SurveyActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survey = close_survey(tenant=request.tenant, survey_id=survey_id, expected_version=serializer.validated_data["expected_version"])
        return Response(survey_data(survey, staff=True))


class SurveyStudentChoicesView(StaffSurveyView):
    def get(self, request):
        return Response({"results": student_choices(tenant=request.tenant, search=request.query_params.get("search", "").strip()[:100])})


def recipient_queryset(request, survey):
    submitted = SurveyResponse.objects.filter(tenant=request.tenant, survey=survey, student_id=OuterRef("student_id"))
    rows = SurveyRecipient.objects.filter(tenant=request.tenant, survey=survey, student__tenant=request.tenant, student__deleted_at__isnull=True).select_related("student").annotate(answered=Exists(submitted)).order_by("student__name", "student_id")
    status = request.query_params.get("response_status", "all")
    if status not in {"all", "answered", "unanswered"}:
        raise ValidationError("응답 상태를 확인해 주세요.")
    if status != "all":
        rows = rows.filter(answered=status == "answered")
    search = request.query_params.get("search", "").strip()[:100]
    if search:
        rows = rows.filter(student__name__icontains=search)
    return rows


@extend_schema_view(get=extend_schema(responses=SurveyRecipientsPageSerializer, parameters=[OpenApiParameter("offset", int), OpenApiParameter("limit", int), OpenApiParameter("search", str), OpenApiParameter("response_status", str, enum=["all", "answered", "unanswered"])]))
class SurveyResponsesView(StaffSurveyView):
    def get(self, request, survey_id):
        survey = staff_survey(request, survey_id)
        offset, limit = pagination(request)
        recipients = recipient_queryset(request, survey)
        count = recipients.count()
        page = list(recipients[offset:offset + limit])
        responses = {row.student_id: row for row in response_queryset(request.tenant).filter(survey=survey, student_id__in=[row.student_id for row in page])}
        return Response({"count": count, "survey": survey_data(survey, staff=True), "results": [{"student_id": row.student_id, "student_name": row.student.name, "answered": row.answered, "response": response_data(responses[row.student_id]) if row.student_id in responses else None} for row in page]})


class SurveyStatisticsView(StaffSurveyView):
    def get(self, request, survey_id):
        survey = staff_survey(request, survey_id)
        questions = [question for question in survey.questions if question["type"] in {"single_choice", "multiple_choice"}]
        counts = {question["id"]: {option: 0 for option in question["options"]} for question in questions}
        if questions:
            rows = SurveyResponse.objects.filter(tenant=request.tenant, survey=survey).values_list("answers", flat=True)
            for answers in rows.iterator(chunk_size=100):
                for question in questions:
                    value = answers.get(question["id"])
                    for choice in value if isinstance(value, list) else [value]:
                        if isinstance(choice, str) and choice in counts[question["id"]]:
                            counts[question["id"]][choice] += 1
        return Response({"response_count": survey.response_count, "target_count": survey.target_count, "questions": [{"id": question["id"], "label": question["label"], "options": [{"label": option, "count": count} for option, count in counts[question["id"]].items()]} for question in questions]})


class SurveyExportThrottle(UserRateThrottle):
    scope = "survey_export"
    rate = "4/min"


def csv_cell(value):
    if isinstance(value, list):
        value = " / ".join(str(item) for item in value)
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


@extend_schema_view(get=extend_schema(responses={(200, "text/csv"): OpenApiTypes.BINARY}, parameters=[OpenApiParameter("response_status", str, enum=["all", "answered", "unanswered"]), OpenApiParameter("search", str)]))
class SurveyExportView(StaffSurveyView):
    throttle_classes = [SurveyExportThrottle]

    def get(self, request, survey_id):
        survey = staff_survey(request, survey_id)
        recipients = recipient_queryset(request, survey)

        def generate():
            yield "\ufeff"
            buffer = io.StringIO()
            writer = csv.writer(buffer)

            def row(values):
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([csv_cell(value) for value in values])
                return buffer.getvalue()

            yield row(["학생", "응답 상태", "수정 시각", *[question["label"] for question in survey.questions], "첨부 자료", "성적 확인 상태"])
            # Bounded chunks keep long free-text answers out of whole-export memory.
            offset = 0
            while True:
                batch = list(recipients[offset:offset + 100])
                if not batch:
                    break
                responses = {value.student_id: value for value in response_queryset(request.tenant).filter(survey=survey, student_id__in=[item.student_id for item in batch])}
                for recipient in batch:
                    response = responses.get(recipient.student_id)
                    material = serialize_response_materials(response) if response else {"attachments": [], "score_submissions": []}
                    yield row([recipient.student.name, "응답 완료" if response else "미응답", timezone.localtime(response.updated_at).isoformat() if response else "", *[(response.answers.get(question["id"], "") if response else "") for question in survey.questions], [item["name"] for item in material["attachments"]], [f"{item['subject']}: {item['status']}" for item in material["score_submissions"]]])
                offset += len(batch)

        result = StreamingHttpResponse(generate(), content_type="text/csv; charset=utf-8")
        result["Content-Disposition"] = f'attachment; filename="survey-{survey.id}.csv"'
        result["Cache-Control"] = "no-store"
        return result


@extend_schema_view(get=extend_schema(operation_id="student_surveys_list", responses=SurveyPageSerializer, parameters=[OpenApiParameter("offset", int), OpenApiParameter("limit", int)]))
class StudentSurveyListView(StudentSurveyView):
    def get(self, request):
        student = self.student(request)
        offset, limit = pagination(request)
        recipients = SurveyRecipient.objects.filter(tenant=request.tenant, student=student).values("survey_id")
        own_response = SurveyResponse.objects.filter(tenant=request.tenant, survey_id=OuterRef("pk"), student=student)
        rows = Survey.objects.filter(tenant=request.tenant, id__in=recipients).exclude(status=Survey.Status.DRAFT)
        count = rows.count()
        rows = with_counts(rows, request.tenant).annotate(own_answered=Exists(own_response))
        return Response({"count": count, "results": [survey_data(survey, has_response=survey.own_answered) for survey in rows[offset:offset + limit]]})


@extend_schema_view(get=extend_schema(responses=StudentSurveyDetailSerializer))
class StudentSurveyDetailView(StudentSurveyView):
    def get(self, request, survey_id):
        student = self.student(request)
        if not SurveyRecipient.objects.filter(tenant=request.tenant, survey_id=survey_id, student=student).exists():
            raise NotFound()
        survey = with_counts(Survey.objects.filter(tenant=request.tenant, id=survey_id).exclude(status=Survey.Status.DRAFT), request.tenant).first()
        if not survey:
            raise NotFound()
        response = response_queryset(request.tenant).filter(survey=survey, student=student).first()
        return Response({"survey": survey_data(survey, has_response=bool(response)), "response": response_data(response) if response else None})


@extend_schema_view(put=extend_schema(request=SurveyResponseWriteSerializer, responses=SurveyResponseReadSerializer))
class StudentSurveyResponseView(StudentSurveyView):
    def put(self, request, survey_id):
        student = self.student(request)
        serializer = SurveyResponseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        saved = save_response(tenant=request.tenant, student=student, user=request.user, survey_id=survey_id, values=serializer.validated_data)
        return Response(response_data(response_queryset(request.tenant).get(id=saved.id)))


@extend_schema_view(get=extend_schema(responses=SurveyHistoryPageSerializer, parameters=[OpenApiParameter("offset", int), OpenApiParameter("limit", int)]))
class StudentSurveyHistoryView(StaffSurveyView):
    def get(self, request, student_id):
        if not active_student(tenant=request.tenant, student_id=student_id):
            raise NotFound()
        offset, limit = pagination(request)
        rows = response_queryset(request.tenant).filter(student_id=student_id)
        return Response({"count": rows.count(), "results": [response_data(row) for row in rows[offset:offset + limit]]})
