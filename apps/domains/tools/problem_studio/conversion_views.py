from __future__ import annotations

import copy
import json
import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.tools.problem_studio.async_transfer import build_source_archive
from apps.domains.tools.problem_studio.conversions import (
    ACTIVE, normalize_options, owned, queue_conversion, refresh_failed_job,
    serialize_conversion, validate_question,
)
from apps.domains.tools.problem_studio.models import ProblemStudioConversion


class ConversionCollectionView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(operation_id="tools_problem_studio_conversions_list", responses=OpenApiTypes.OBJECT, parameters=[OpenApiParameter("offset", int)], tags=["tools"])
    def get(self, request):
        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            raise ValidationError("목록 위치를 확인해 주세요.")
        if offset < 0 or offset > 1_000_000:
            raise ValidationError("목록 위치를 확인해 주세요.")
        rows = list(owned(request)[offset:offset + 61])
        return Response({
            "conversions": [serialize_conversion(refresh_failed_job(row), detail=False) for row in rows[:60]],
            "next_offset": offset + 60 if len(rows) > 60 else None,
        })

    @extend_schema(request={"multipart/form-data": {"type": "object", "required": ["payload", "source_files"], "properties": {
        "payload": {"type": "string", "description": "JSON title and options; external_ai_confirmed must be true."},
        "source_files": {"type": "array", "items": {"type": "string", "format": "binary"}},
    }}}, responses={202: OpenApiTypes.OBJECT}, tags=["tools"])
    def post(self, request):
        try:
            raw = request.data.get("payload", {})
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            raise ValidationError("변환 설정을 확인해 주세요.")
        if not isinstance(payload, dict):
            raise ValidationError("변환 설정을 확인해 주세요.")
        options = normalize_options(payload.get("options"))
        files = request.FILES.getlist("source_files")
        if not files or any(str(f.name).rsplit(".", 1)[-1].lower() not in {"pdf", "png", "jpg", "jpeg", "webp"} for f in files):
            raise ValidationError("PDF 또는 PNG·JPG·WebP 이미지를 올려 주세요.")
        title = (str(payload.get("title") or "").strip() or files[0].name.rsplit(".", 1)[0] or "시험지 변환")[:160]
        conversion_id = uuid.uuid4()
        key = f"tenants/{request.tenant.id}/tools/problem-studio/conversions/{conversion_id}/sources.zip"
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage, delete_object_r2_storage
        try:
            archive, manifest = build_source_archive(files)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            upload_fileobj_to_r2_storage(fileobj=archive, key=key, content_type="application/zip")
        finally:
            archive.close()
        try:
            with transaction.atomic():
                row = ProblemStudioConversion.objects.create(
                    id=conversion_id, tenant=request.tenant, owner=request.user, title=title,
                    options=options, source_archive_key=key, source_files=manifest,
                )
                queue_conversion(row, {"action": "convert"})
        except Exception:
            delete_object_r2_storage(key=key)
            raise
        row.refresh_from_db()
        return Response(serialize_conversion(row), status=202)


class ConversionDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=OpenApiTypes.OBJECT, tags=["tools"])
    def get(self, request, conversion_id):
        row = get_object_or_404(owned(request), id=conversion_id)
        return Response(serialize_conversion(refresh_failed_job(row)))

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT}, tags=["tools"])
    def patch(self, request, conversion_id):
        with transaction.atomic():
            row = get_object_or_404(owned(request).select_for_update(), id=conversion_id)
            refresh_failed_job(row)
            if request.data.get("version") != row.version:
                return Response({"detail": "다른 화면에서 수정되었습니다. 입력을 보관한 뒤 최신 내용을 다시 열어 주세요."}, status=409)
            if row.status in ACTIVE:
                return Response({"detail": "작업이 끝난 뒤 수정해 주세요."}, status=409)
            document = copy.deepcopy(row.document)
            questions = document.get("questions", [])
            action = request.data.get("action", "edit")
            if action == "accept_all":
                for question in questions:
                    question["reviewed"] = True
                    question.pop("proposal", None)
            else:
                question = next((q for q in questions if q["id"] == request.data.get("question_id")), None)
                if question is None:
                    raise ValidationError("문항을 찾을 수 없습니다.")
                if action == "accept":
                    question["reviewed"] = True
                    question.pop("proposal", None)
                elif action == "apply_proposal":
                    proposal = question.get("proposal")
                    if not proposal:
                        raise ValidationError("적용할 수정 제안이 없습니다.")
                    revised = validate_question(proposal, question)
                    if proposal.get("redrawn"):
                        revised["redrawn"] = proposal["redrawn"]
                    questions[questions.index(question)] = revised
                elif action == "discard_proposal":
                    question.pop("proposal", None)
                elif action == "edit":
                    revised = validate_question(request.data.get("question"), question)
                    page_index = revised.get("page_index")
                    if page_index is not None and (type(page_index) is not int or not 0 <= page_index < len(document.get("pages", []))):
                        raise ValidationError("원본 페이지를 확인해 주세요.")
                    questions[questions.index(question)] = revised
                else:
                    raise ValidationError("지원하지 않는 검수 동작입니다.")
            row.document = document
            row.version += 1
            row.status = "REVIEW" if any(not q.get("reviewed") for q in questions) else "DONE"
            row.save(update_fields=["document", "version", "status", "updated_at"])
        return Response(serialize_conversion(row))


class ConversionActionView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(request=OpenApiTypes.OBJECT, responses={202: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT}, tags=["tools"])
    def post(self, request, conversion_id):
        with transaction.atomic():
            row = get_object_or_404(owned(request).select_for_update(), id=conversion_id)
            refresh_failed_job(row)
            if row.status in ACTIVE:
                return Response(serialize_conversion(row), status=202)
            if request.data.get("version") != row.version:
                return Response({"detail": "최신 내용을 다시 열어 주세요."}, status=409)
            action = request.data.get("action")
            if action == "resume":
                if row.status != "FAILED":
                    raise ValidationError("중단된 작업만 이어서 할 수 있습니다.")
                operation = {k: v for k, v in row.operation.items() if k not in {"token", "version"}}
            elif action == "rebuild":
                if not row.document.get("questions"):
                    raise ValidationError("변환된 문항이 없습니다.")
                if row.options.get("require_review") and any(not q.get("reviewed") for q in row.document["questions"]):
                    raise ValidationError("설정에 따라 모든 문항의 확인을 마쳐야 문서를 만들 수 있습니다.")
                operation = {"action": "rebuild"}
            elif action in {"correct", "reanalyze", "redraw"}:
                question_id = request.data.get("question_id")
                if not any(q["id"] == question_id for q in row.document.get("questions", [])):
                    raise ValidationError("문항을 찾을 수 없습니다.")
                instruction = request.data.get("instruction", "")
                if not isinstance(instruction, str) or len(instruction) > 2000 or (action == "correct" and not instruction.strip()):
                    raise ValidationError("고칠 내용을 2,000자 이내로 입력해 주세요.")
                operation = {"action": action, "question_id": question_id, "instruction": instruction}
            else:
                raise ValidationError("지원하지 않는 작업입니다.")
            queue_conversion(row, operation)
        row.refresh_from_db()
        return Response(serialize_conversion(row), status=202)
