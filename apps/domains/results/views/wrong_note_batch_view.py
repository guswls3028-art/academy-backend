from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import WrongNoteBatch, WrongNotePDF
from apps.domains.results.services.range_wrong_note_service import RangeSelectionError, WrongNoteRange
from apps.domains.results.services.wrong_note_batch_archive import batch_archive_key, student_filename
from apps.domains.results.services.wrong_note_batch_service import (
    batch_status, create_batch, current_documents, retry_batch,
)
from apps.domains.results.services.wrong_note_pdf_service import wrong_note_pdf_storage_key
from apps.domains.results.throttles import WrongNotePDFCreateThrottle
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
from apps.support.results.wrong_note_pdf_dependencies import lecture_exists_for_tenant


class RangeParams(serializers.Serializer):
    lecture_id = serializers.IntegerField(min_value=1)
    from_session_order = serializers.IntegerField(min_value=1)
    to_session_order = serializers.IntegerField(min_value=1)

    def validate(self, data):
        if data["from_session_order"] > data["to_session_order"]:
            raise serializers.ValidationError("종료 차시는 시작 차시 이후여야 합니다.")
        return data


class SourceSelection(serializers.Serializer):
    type = serializers.ChoiceField(choices=["exam", "homework"])
    id = serializers.IntegerField(min_value=1)
    numbers = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=100,
    )


class StudentSelection(serializers.Serializer):
    enrollment_id = serializers.IntegerField(min_value=1)
    sources = SourceSelection(many=True, allow_empty=False, max_length=100)


class BatchInput(RangeParams):
    students = StudentSelection(many=True, allow_empty=False, max_length=200)


class RangeStudent(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    name = serializers.CharField()


class RangeSource(serializers.Serializer):
    type = serializers.ChoiceField(choices=["exam", "homework"])
    id = serializers.IntegerField()
    title = serializers.CharField()
    session_order = serializers.IntegerField()
    question_numbers = serializers.ListField(child=serializers.IntegerField())
    unavailable_numbers = serializers.ListField(child=serializers.IntegerField())
    enrollment_ids = serializers.ListField(child=serializers.IntegerField())
    ready = serializers.BooleanField()


class WrongNoteRangeResponse(RangeParams):
    students = RangeStudent(many=True)
    sources = RangeSource(many=True)


class BatchStudentStatus(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=WrongNotePDF.Status.choices)
    error_message = serializers.CharField(allow_blank=True)


class BatchResponse(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=WrongNotePDF.Status.choices)
    students = BatchStudentStatus(many=True)
    detail = serializers.CharField(allow_blank=True)


class BatchDownload(serializers.Serializer):
    url = serializers.URLField()
    filename = serializers.CharField()


def get_range(request, params):
    lecture_id = params["lecture_id"]
    if not lecture_exists_for_tenant(lecture_id=lecture_id, tenant=request.tenant):
        raise NotFound("강의를 찾을 수 없습니다.")
    return WrongNoteRange(
        tenant_id=request.tenant.pk, lecture_id=lecture_id,
        first=params["from_session_order"], last=params["to_session_order"],
    )


class WrongNoteRangeView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(parameters=[RangeParams], responses=WrongNoteRangeResponse)
    def get(self, request):
        form = RangeParams(data=request.query_params)
        form.is_valid(raise_exception=True)
        scope = get_range(request, form.validated_data)
        return Response({
            **form.validated_data, "sources": [s.info for s in scope.sources.values()],
            "students": [{"enrollment_id": e.pk, "student_id": e.student_id, "name": e.student.name}
                         for e in scope.students.values()],
        })


class WrongNoteBatchCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    throttle_classes = [WrongNotePDFCreateThrottle]

    @extend_schema(request=BatchInput, responses={202: BatchResponse})
    def post(self, request):
        form = BatchInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        scope = get_range(request, data)
        try:
            batch = create_batch(
                scope=scope, tenant_id=request.tenant.pk, lecture_id=data["lecture_id"],
                first=data["from_session_order"], last=data["to_session_order"], students=data["students"],
            )
        except RangeSelectionError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(batch_status(batch, request.tenant.pk), status=202)


class WrongNoteBatchDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_batch(self, request, batch_id):
        return get_object_or_404(WrongNoteBatch, pk=batch_id, lecture__tenant_id=request.tenant.pk)

    @extend_schema(responses=BatchResponse)
    def get(self, request, batch_id):
        return Response(batch_status(self.get_batch(request, batch_id), request.tenant.pk))


class WrongNoteBatchRetryView(WrongNoteBatchDetailView):
    http_method_names = ["post", "options"]
    throttle_classes = [WrongNotePDFCreateThrottle]

    @extend_schema(request=None, responses={202: BatchResponse})
    def post(self, request, batch_id):
        batch = self.get_batch(request, batch_id)
        try:
            retry_batch(batch, request.tenant.pk)
        except RangeSelectionError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(batch_status(batch, request.tenant.pk), status=202)


class WrongNoteBatchDownloadView(WrongNoteBatchDetailView):
    @extend_schema(responses=BatchDownload)
    def get(self, request, batch_id):
        batch = self.get_batch(request, batch_id)
        docs = current_documents(batch, request.tenant.pk)
        if len(docs) != batch.student_count or not docs or any(d.status != WrongNotePDF.Status.DONE for d in docs):
            return Response({"detail": "모든 학생의 오답노트가 완료된 후 내려받을 수 있습니다."}, status=409)
        if len(docs) == 1:
            doc = docs[0]
            key = wrong_note_pdf_storage_key(job=doc, tenant=request.tenant)
            if key != doc.file_path:
                raise NotFound("파일을 찾을 수 없습니다.")
            filename, content_type = student_filename(doc), "application/pdf"
        else:
            key = batch_archive_key(batch.pk, request.tenant.pk, docs)
            filename = f"{docs[0].from_session_order}-{docs[0].to_session_order}차시_학생별_오답노트.zip"
            content_type = "application/zip"
        url = generate_presigned_get_url_storage(
            key=key, filename=filename, content_type=content_type, expires_in=600,
        )
        return Response({"url": url, "filename": filename}, headers={"Cache-Control": "private, no-store"})
