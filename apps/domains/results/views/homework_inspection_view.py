from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models.homework_inspection import HomeworkInspection
from apps.domains.results.services.assessment_scope import assessment_scope
from apps.domains.results.services.homework_inspection_excel import MAX_ROWS, build_template, read_preview
from apps.support.results.parent_comment_dependencies import Homework, HomeworkAssignment


class InspectionConflict(APIException):
    status_code = 409


class InspectionChange(serializers.Serializer):
    enrollment_id = serializers.IntegerField(min_value=1)
    version = serializers.IntegerField(min_value=0)
    solved = serializers.IntegerField(min_value=0, max_value=100000, required=False, allow_null=True)
    total = serializers.IntegerField(min_value=1, max_value=100000, required=False, allow_null=True)
    feedback = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    def validate(self, attrs):
        if ("solved" in attrs) != ("total" in attrs):
            raise ValidationError("풀이 수와 총 문제 수를 함께 입력해 주세요.")
        if "solved" in attrs:
            solved, total = attrs["solved"], attrs["total"]
            if (solved is None) != (total is None) or (solved is not None and solved > total):
                raise ValidationError("풀이 수와 총 문제 수를 확인해 주세요.")
        elif "feedback" not in attrs:
            raise ValidationError("풀이 수 또는 코멘트를 입력해 주세요.")
        return attrs


class InspectionBatch(serializers.Serializer):
    rows = InspectionChange(many=True, allow_empty=False, max_length=MAX_ROWS)


class InspectionRosterRow(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    solved = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True)
    feedback = serializers.CharField(allow_blank=True)
    version = serializers.IntegerField()


class InspectionPreviewRow(serializers.Serializer):
    line = serializers.IntegerField()
    name = serializers.CharField()
    before = InspectionRosterRow()
    change = InspectionChange()
    status = serializers.ChoiceField(choices=["변경", "새 입력", "동일"])


def inspection_scope(request, session_id, homework_id):
    _, enrollments = assessment_scope(request.tenant, session_id, "homework", homework_id)
    return enrollments


def records(tenant, session_id, homework_id):
    return HomeworkInspection.objects.filter(tenant=tenant, session_id=session_id, homework_id=homework_id)


def roster(request, session_id, homework_id):
    enrollments = inspection_scope(request, session_id, homework_id)
    saved = {row.enrollment_id: row for row in records(request.tenant, session_id, homework_id).filter(enrollment__in=enrollments)}
    items = []
    for eid, name in enrollments.order_by("student__name", "id").values_list("id", "student__name"):
        row = saved.get(eid)
        items.append({"enrollment_id": eid, "student_name": name, "solved": row.solved if row else None,
                      "total": row.total if row else None, "feedback": row.feedback if row else "",
                      "version": row.version if row else 0})
    return items


class HomeworkInspectionView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=inline_serializer("InspectionRoster", fields={"items": InspectionRosterRow(many=True)}))
    def get(self, request, session_id, homework_id):
        return Response({"items": roster(request, session_id, homework_id)})

    @extend_schema(request=InspectionBatch, responses=inline_serializer("InspectionResult", fields={"changed": serializers.IntegerField()}))
    @transaction.atomic
    def post(self, request, session_id, homework_id):
        serializer = InspectionBatch(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = serializer.validated_data["rows"]
        ids = {row["enrollment_id"] for row in changes}
        if len(ids) != len(changes):
            raise ValidationError({"detail": "명단에 같은 학생이 여러 번 있습니다."})
        enrollments = inspection_scope(request, session_id, homework_id)
        targets = HomeworkAssignment.objects.filter(
            tenant=request.tenant, session_id=session_id, homework_id=homework_id,
            enrollment_id__in=enrollments.filter(pk__in=ids).values("pk"),
        ).order_by("enrollment_id").select_for_update()
        if set(targets.values_list("enrollment_id", flat=True)) != ids:
            raise InspectionConflict("과제 배정 명단이 바뀌었습니다. 최신 명단을 확인해 주세요.")
        saved = {row.enrollment_id: row for row in records(request.tenant, session_id, homework_id).filter(enrollment_id__in=ids)}
        created, updated = [], []
        for change in changes:
            eid = change["enrollment_id"]
            row = saved.get(eid) or HomeworkInspection(tenant=request.tenant, session_id=session_id, homework_id=homework_id, enrollment_id=eid)
            fields = {key: change[key] for key in ("solved", "total", "feedback") if key in change}
            if all(getattr(row, key) == value for key, value in fields.items()):
                continue
            if row.version != change["version"]:
                raise InspectionConflict("다른 교직원이 수정했습니다. 최신 값 확인 후 다시 반영해 주세요.")
            for key, value in fields.items():
                setattr(row, key, value)
            row.version += 1
            row.updated_by = request.user
            row.updated_at = timezone.now()
            (created if row.pk is None else updated).append(row)
        HomeworkInspection.objects.bulk_create(created, batch_size=300)
        HomeworkInspection.objects.bulk_update(updated, ["solved", "total", "feedback", "version", "updated_by", "updated_at"], batch_size=200)
        return Response({"changed": len(created) + len(updated)})


class HomeworkInspectionWorkbookView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    parser_classes = [MultiPartParser]

    @extend_schema(parameters=[OpenApiParameter("total", OpenApiTypes.INT, description="Optional shared problem count for the template.")],
                   responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiTypes.BINARY})
    def get(self, request, session_id, homework_id):
        items = roster(request, session_id, homework_id)
        title = Homework.objects.values_list("title", flat=True).get(pk=homework_id, tenant=request.tenant, session_id=session_id)
        total = request.query_params.get("total") or None
        if total is not None:
            total = serializers.IntegerField(min_value=1, max_value=100000).run_validation(total)
        data = build_template((request.tenant.pk, session_id, homework_id), title, items, total)
        response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="homework-{homework_id}.xlsx"'
        return response

    @extend_schema(request=inline_serializer("InspectionUpload", fields={"file": serializers.FileField()}),
                   responses=inline_serializer("InspectionPreview", fields={
                       "rows": InspectionPreviewRow(many=True), "errors": serializers.ListField(child=serializers.CharField()),
                   }))
    def post(self, request, session_id, homework_id):
        items = roster(request, session_id, homework_id)
        upload = request.FILES.get("file")
        if not upload or not upload.name.lower().endswith(".xlsx"):
            raise ValidationError({"detail": ".xlsx 형식의 과제 검사 파일을 선택해 주세요."})
        try:
            preview = read_preview(upload, (request.tenant.pk, session_id, homework_id), items)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(preview)
