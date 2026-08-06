from __future__ import annotations

import io
import json
import logging
import uuid
from pathlib import Path

try:
    from drf_spectacular.utils import extend_schema
except ModuleNotFoundError as exc:
    if exc.name != "drf_spectacular":
        raise

    def extend_schema(*args, **kwargs):  # type: ignore[no-redef]
        """Keep runtime views importable when schema tooling is absent."""

        def decorator(view):
            return view

        return decorator


from django.db import models, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academy.adapters.db.django import repositories_ai as ai_repo
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.landing_public.contracts import (
    hide_problem_review_showcase,
    publish_problem_review_showcase,
)
from apps.domains.tools.problem_review.readiness import build_review_readiness, report_fingerprint
from apps.domains.tools.problem_review.schema import normalize_report_payload
from apps.domains.tools.problem_review.serializers import (
    ProblemReviewExportCreateSerializer,
    ProblemReviewExportRequestSerializer,
    ProblemReviewExportStatusSerializer,
    ProblemReviewFinalizeRequestSerializer,
    ProblemReviewReportCreateSerializer,
    ProblemReviewReportListSerializer,
    ProblemReviewReportPatchSerializer,
    ProblemReviewPublishRequestSerializer,
    ProblemReviewPublishResponseSerializer,
    ProblemReviewReportSerializer,
)
from apps.domains.tools.problem_studio.async_transfer import (
    SOURCE_ARCHIVE_MAX_TOTAL_BYTES,
    build_source_archive,
)
from apps.domains.tools.problem_studio.transfer_documents import TRANSFER_MAX_UPLOAD_BYTES
from apps.domains.tools.problem_studio.models import ProblemReviewArtifact, ProblemReviewReport
from apps.domains.tools.problem_review.renderers import render_problem_review_report
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


ANALYSIS_JOB_TYPE = "problem_review_analysis"
EXPORT_JOB_TYPE = "problem_review_export"
MAX_SOURCE_FILES = 6
SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
logger = logging.getLogger(__name__)


class _ProblemReviewPublishConflict(Exception):
    pass


def _truthy(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _metadata(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("시험 정보 형식이 올바르지 않습니다.") from exc
        if isinstance(parsed, dict):
            return parsed
    return {}


def _job_owned_by_report(job, report: ProblemReviewReport) -> bool:
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    return (
        str(payload.get("request_user_id") or "") == str(report.requested_by_id or "")
        and str(getattr(job, "source_id", "") or "") == str(report.id)
        and str(getattr(job, "source_domain", "") or "") == "tools_problem_review"
    )


def _refresh_analysis(report: ProblemReviewReport) -> ProblemReviewReport:
    if report.status != ProblemReviewReport.Status.ANALYZING or not report.analysis_job_id:
        return report
    job = ai_repo.get_job_model_for_status(
        report.analysis_job_id,
        str(report.tenant_id),
        job_type=ANALYSIS_JOB_TYPE,
    )
    if not job or not _job_owned_by_report(job, report):
        return report
    if job.status == "DONE":
        result = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
        draft = normalize_report_payload(result.get("report"))
        if draft.get("questions"):
            report.draft = draft
            report.source_summary = result.get("source") if isinstance(result.get("source"), dict) else {}
            report.status = ProblemReviewReport.Status.DRAFT
            report.last_error = ""
            report.save(update_fields=["draft", "source_summary", "status", "last_error", "updated_at"])
    elif job.status in {"FAILED", "DEAD", "CANCELLED"}:
        report.status = ProblemReviewReport.Status.FAILED
        report.last_error = str(job.error_message or job.last_error or "분석 작업에 실패했습니다.")[:2000]
        report.save(update_fields=["status", "last_error", "updated_at"])
    return report


def _serialize_report(report: ProblemReviewReport, *, include_draft: bool = True) -> dict:
    payload = {
        "id": str(report.id),
        "status": report.status,
        "title": report.title,
        "source_name": report.source_name,
        "source_summary": report.source_summary,
        "version": report.version,
        "last_error": report.last_error,
        "created_at": report.created_at.isoformat(),
        "updated_at": report.updated_at.isoformat(),
        "artifacts": [
            _serialize_artifact(artifact)
            for artifact in report.artifacts.all()[:12]
        ],
        "review_readiness": build_review_readiness(
            report.draft,
            finalized_fingerprint=report.review_fingerprint,
            finalized_at=report.review_completed_at,
        ) if report.draft else None,
    }
    if include_draft:
        payload["draft"] = report.draft
    return payload


def _snapshot_fingerprint(draft: dict) -> tuple[dict, str]:
    return report_fingerprint(draft)


def _serialize_artifact(artifact: ProblemReviewArtifact, *, include_download: bool = False) -> dict:
    payload = {
        "id": str(artifact.id),
        "job_id": artifact.job_id,
        "status": artifact.status,
        "output_format": artifact.output_format,
        "report_version": artifact.report_version,
        "source_fingerprint": artifact.source_fingerprint,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "error_message": artifact.error_message,
        "verified": bool(artifact.review_completed_at),
        "created_at": artifact.created_at.isoformat(),
        "updated_at": artifact.updated_at.isoformat(),
    }
    if (
        include_download
        and artifact.status == ProblemReviewArtifact.Status.READY
        and artifact.r2_key
        and artifact.review_completed_at
    ):
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        payload["download_url"] = generate_presigned_get_url_storage(
            key=artifact.r2_key,
            expires_in=900,
            filename=artifact.filename or "problem-review-report",
            content_type=artifact.content_type or "application/octet-stream",
        )
    return payload


def _get_owned_report(request, report_id) -> ProblemReviewReport | None:
    return ProblemReviewReport.objects.filter(
        pk=report_id,
        tenant=request.tenant,
        requested_by=request.user,
    ).first()


def _public_snapshot(draft: dict, *, verified_at=None, fingerprint: str = "") -> dict:
    """Whitelist fields suitable for a public article and PDF."""
    normalized = normalize_report_payload(
        draft,
        preserve_question_set=False,
        preserve_review_status=True,
    )
    public_questions = [
        {
            "number": item.get("number"),
            "source_number": item.get("source_number"),
            "unit": item.get("unit"),
            "answer": item.get("answer"),
            "points": item.get("points"),
            "difficulty": item.get("difficulty"),
            "thinking_action": item.get("thinking_action"),
            "key_point": item.get("key_point"),
            "trap": item.get("trap"),
        }
        for item in normalized.get("questions", [])
    ]
    return {
        "schema_version": normalized.get("schema_version"),
        "metadata": normalized.get("metadata") or {},
        "summary": normalized.get("summary") or {},
        "assessment_axes": normalized.get("assessment_axes") or [],
        "domains": normalized.get("domains") or [],
        "difficulty": normalized.get("difficulty") or {},
        "questions": public_questions,
        "key_items": normalized.get("key_items") or [],
        "failure_patterns": normalized.get("failure_patterns") or [],
        "recovery_protocol": normalized.get("recovery_protocol") or {},
        "achievement_bands": normalized.get("achievement_bands") or [],
        "parent_guidance": normalized.get("parent_guidance") or {},
        "conclusion": normalized.get("conclusion") or {},
        "verification": {
            "status": "verified",
            "verified_at": verified_at.isoformat() if hasattr(verified_at, "isoformat") else verified_at,
            "report_fingerprint": fingerprint,
        } if verified_at and fingerprint else {},
    }


class ProblemReviewReportCollectionView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        operation_id="tools_problem_review_report_list",
        responses=ProblemReviewReportListSerializer,
    )
    def get(self, request):
        reports = list(
            ProblemReviewReport.objects.filter(
                tenant=request.tenant,
                requested_by=request.user,
            ).prefetch_related("artifacts")[:20]
        )
        for report in reports:
            _refresh_analysis(report)
        response = Response({
            "reports": [_serialize_report(report, include_draft=False) for report in reports],
        })
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        operation_id="tools_problem_review_report_create",
        request=ProblemReviewReportCreateSerializer,
        responses={202: ProblemReviewReportSerializer},
    )
    def post(self, request):
        if not _truthy(request.data.get("external_ai_confirmed")):
            return Response(
                {"detail": "시험지 판독과 분석을 위해 외부 AI 처리 안내를 확인해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        source_files = request.FILES.getlist("source_files")
        if not source_files:
            return Response(
                {"detail": "리뷰할 시험지나 문제지 파일을 올려 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(source_files) > MAX_SOURCE_FILES:
            return Response(
                {"detail": f"한 번에 파일은 {MAX_SOURCE_FILES}개까지 올릴 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invalid_names = [
            str(file.name)
            for file in source_files
            if Path(str(file.name)).suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES
        ]
        if invalid_names:
            return Response(
                {"detail": f"지원하지 않는 파일 형식입니다: {', '.join(invalid_names[:3])}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        oversized = [str(file.name) for file in source_files if int(getattr(file, "size", 0) or 0) > TRANSFER_MAX_UPLOAD_BYTES]
        if oversized:
            return Response(
                {"detail": f"파일 하나는 120MB까지 올릴 수 있습니다: {', '.join(oversized[:3])}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        total_bytes = sum(int(getattr(file, "size", 0) or 0) for file in source_files)
        if total_bytes > SOURCE_ARCHIVE_MAX_TOTAL_BYTES:
            return Response(
                {"detail": "전체 파일 용량은 512MB까지 올릴 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            metadata = _metadata(request.data.get("metadata"))
            archive_file, source_manifest = build_source_archive(source_files)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        title = str(metadata.get("title") or "").strip()[:200]
        source_name = str(source_manifest[0]["name"] if source_manifest else "")[:255]
        report = ProblemReviewReport.objects.create(
            tenant=request.tenant,
            requested_by=request.user,
            status=ProblemReviewReport.Status.ANALYZING,
            title=title,
            source_name=source_name,
            source_summary={
                "file_count": len(source_manifest),
                "files": [
                    {"name": item["name"], "size_bytes": item["size"]}
                    for item in source_manifest
                ],
            },
        )
        archive_key = f"tenants/{request.tenant.id}/tools/problem-review/tmp/{report.id}/sources.zip"
        uploaded = False
        try:
            from apps.infrastructure.storage.r2 import (
                delete_object_r2_storage,
                upload_fileobj_to_r2_storage,
            )

            upload_fileobj_to_r2_storage(
                fileobj=archive_file,
                key=archive_key,
                content_type="application/zip",
            )
            uploaded = True
            result = dispatch_tools_ai_job(
                job_type=ANALYSIS_JOB_TYPE,
                payload={
                    "report_id": str(report.id),
                    "tenant_id": str(request.tenant.id),
                    "request_user_id": str(request.user.id),
                    "source_archive_key": archive_key,
                    "source_files": source_manifest,
                    "metadata": metadata,
                },
                tenant_id=str(request.tenant.id),
                source_domain="tools_problem_review",
                source_id=str(report.id),
                tier="basic",
            )
            if not result.get("ok"):
                try:
                    delete_object_r2_storage(key=archive_key)
                except Exception:
                    pass
                report.status = ProblemReviewReport.Status.FAILED
                report.last_error = str(result.get("error") or "분석 작업을 시작할 수 없습니다.")[:2000]
                report.save(update_fields=["status", "last_error", "updated_at"])
                return Response(
                    {"detail": report.last_error, "report": _serialize_report(report)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            report.analysis_job_id = str(result["job_id"])
            report.save(update_fields=["analysis_job_id", "updated_at"])
        except Exception as exc:
            if uploaded:
                try:
                    delete_object_r2_storage(key=archive_key)
                except Exception:
                    pass
            report.status = ProblemReviewReport.Status.FAILED
            report.last_error = str(exc)[:2000]
            report.save(update_fields=["status", "last_error", "updated_at"])
            return Response(
                {"detail": "분석 작업을 시작하지 못했습니다.", "report": _serialize_report(report)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        finally:
            archive_file.close()

        response = Response(_serialize_report(report), status=status.HTTP_202_ACCEPTED)
        response["Cache-Control"] = "no-store"
        return response


class ProblemReviewReportDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="tools_problem_review_report_retrieve",
        responses=ProblemReviewReportSerializer,
    )
    def get(self, request, report_id):
        report = _get_owned_report(request, report_id)
        if report is None:
            return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        report = _refresh_analysis(report)
        response = Response(_serialize_report(report))
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        operation_id="tools_problem_review_report_update",
        request=ProblemReviewReportPatchSerializer,
        responses=ProblemReviewReportSerializer,
    )
    def patch(self, request, report_id):
        if not isinstance(request.data, dict):
            return Response({"detail": "저장할 초안이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            expected_version = int(request.data.get("version"))
        except (TypeError, ValueError):
            return Response({"detail": "현재 리포트 버전을 확인해 주세요."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            report = ProblemReviewReport.objects.select_for_update().filter(
                pk=report_id,
                tenant=request.tenant,
                requested_by=request.user,
            ).first()
            if report is None:
                return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
            report = _refresh_analysis(report)
            if report.status != ProblemReviewReport.Status.DRAFT:
                return Response({"detail": "분석이 끝난 뒤 초안을 저장할 수 있습니다."}, status=status.HTTP_409_CONFLICT)
            if report.version != expected_version:
                return Response(
                    {"detail": "다른 화면에서 리포트가 수정되었습니다.", "report": _serialize_report(report)},
                    status=status.HTTP_409_CONFLICT,
                )
            report.draft = normalize_report_payload(
                request.data.get("draft"),
                fallback=report.draft,
                preserve_question_set=False,
                preserve_review_status=True,
            )
            report.title = str(request.data.get("title") or report.draft.get("metadata", {}).get("title") or report.title)[:200]
            report.version += 1
            report.review_completed_at = None
            report.review_completed_by = None
            report.review_fingerprint = ""
            report.save(update_fields=[
                "draft",
                "title",
                "version",
                "review_completed_at",
                "review_completed_by",
                "review_fingerprint",
                "updated_at",
            ])
        response = Response(_serialize_report(report))
        response["Cache-Control"] = "no-store"
        return response


class ProblemReviewFinalizeView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="tools_problem_review_finalize",
        request=ProblemReviewFinalizeRequestSerializer,
        responses=ProblemReviewReportSerializer,
    )
    def post(self, request, report_id):
        try:
            expected_version = int(request.data.get("version"))
        except (AttributeError, TypeError, ValueError):
            return Response(
                {"detail": "현재 리포트 버전을 확인해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            report = ProblemReviewReport.objects.select_for_update().filter(
                pk=report_id,
                tenant=request.tenant,
                requested_by=request.user,
            ).first()
            if report is None:
                return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
            report = _refresh_analysis(report)
            if report.status != ProblemReviewReport.Status.DRAFT or not report.draft:
                return Response(
                    {"detail": "검수 초안이 준비된 뒤 최종 확정할 수 있습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            if report.version != expected_version:
                return Response(
                    {"detail": "다른 화면에서 수정되었습니다. 최신 리포트를 다시 열어 주세요."},
                    status=status.HTTP_409_CONFLICT,
                )
            readiness = build_review_readiness(report.draft)
            if not readiness["ready_for_finalize"]:
                return Response(
                    {
                        "detail": "남은 검수 항목을 확인한 뒤 최종 확정해 주세요.",
                        "review_readiness": readiness,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            report.review_completed_at = timezone.now()
            report.review_completed_by = request.user
            report.review_fingerprint = readiness["fingerprint"]
            report.save(update_fields=[
                "review_completed_at",
                "review_completed_by",
                "review_fingerprint",
                "updated_at",
            ])
        response = Response(_serialize_report(report))
        response["Cache-Control"] = "no-store"
        return response


class ProblemReviewExportCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="tools_problem_review_export_create",
        request=ProblemReviewExportRequestSerializer,
        responses={202: ProblemReviewExportCreateSerializer},
    )
    def post(self, request, report_id):
        report = _get_owned_report(request, report_id)
        if report is None:
            return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        report = _refresh_analysis(report)
        if report.status != ProblemReviewReport.Status.DRAFT or not report.draft:
            return Response({"detail": "검수 초안이 준비된 뒤 다운로드할 수 있습니다."}, status=status.HTTP_409_CONFLICT)
        readiness = build_review_readiness(
            report.draft,
            finalized_fingerprint=report.review_fingerprint,
            finalized_at=report.review_completed_at,
        )
        if not readiness["is_finalized"]:
            return Response(
                {
                    "detail": "전 문항 원문·정답 대조와 최종 검수 확정 뒤 다운로드할 수 있습니다.",
                    "review_readiness": readiness,
                },
                status=status.HTTP_409_CONFLICT,
            )
        output_format = str(request.data.get("output_format") or "").lower()
        if output_format not in {"pdf", "pptx"}:
            return Response({"detail": "PDF 또는 PPTX만 선택할 수 있습니다."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot, fingerprint = _snapshot_fingerprint(report.draft)
        artifact, created = ProblemReviewArtifact.objects.get_or_create(
            tenant=request.tenant,
            report=report,
            created_by=request.user,
            output_format=output_format,
            report_version=report.version,
            source_fingerprint=fingerprint,
            defaults={"review_completed_at": report.review_completed_at},
        )
        if not created and artifact.status == ProblemReviewArtifact.Status.READY:
            response = Response(_serialize_artifact(artifact, include_download=True), status=status.HTTP_200_OK)
            response["Cache-Control"] = "no-store"
            return response
        if not created and artifact.status == ProblemReviewArtifact.Status.PENDING and artifact.job_id:
            response = Response(_serialize_artifact(artifact), status=status.HTTP_202_ACCEPTED)
            response["Cache-Control"] = "no-store"
            return response
        artifact.status = ProblemReviewArtifact.Status.PENDING
        artifact.review_completed_at = report.review_completed_at
        artifact.error_message = ""
        artifact.job_id = ""
        artifact.save(update_fields=[
            "status",
            "error_message",
            "job_id",
            "review_completed_at",
            "updated_at",
        ])
        result = dispatch_tools_ai_job(
            job_type=EXPORT_JOB_TYPE,
            payload={
                "report_id": str(report.id),
                "report_version": report.version,
                "report": snapshot,
                "output_format": output_format,
                "tenant_id": str(request.tenant.id),
                "request_user_id": str(request.user.id),
                "artifact_id": str(artifact.id),
                "source_fingerprint": fingerprint,
                "review_completed_at": report.review_completed_at.isoformat(),
            },
            tenant_id=str(request.tenant.id),
            source_domain="tools_problem_review",
            source_id=str(report.id),
            tier="basic",
            idempotency_key=f"problem-review-export:{artifact.id}",
            force_rerun=not created,
            rerun_reason="retry_failed_problem_review_artifact" if not created else None,
        )
        if not result.get("ok"):
            artifact.status = ProblemReviewArtifact.Status.FAILED
            artifact.error_message = str(result.get("error") or "다운로드 파일을 만들 수 없습니다.")[:2000]
            artifact.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"detail": artifact.error_message},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        artifact.job_id = str(result["job_id"])
        artifact.save(update_fields=["job_id", "updated_at"])
        response = Response(
            _serialize_artifact(artifact),
            status=status.HTTP_202_ACCEPTED,
        )
        response["Cache-Control"] = "no-store"
        return response


class ProblemReviewExportStatusView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="tools_problem_review_export_status",
        responses=ProblemReviewExportStatusSerializer,
    )
    def get(self, request, report_id, job_id: str):
        report = _get_owned_report(request, report_id)
        if report is None:
            return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        artifact_lookup = models.Q(job_id=str(job_id))
        try:
            artifact_lookup |= models.Q(pk=uuid.UUID(str(job_id)))
        except (TypeError, ValueError):
            pass
        artifact = ProblemReviewArtifact.objects.filter(
            report=report,
            tenant=request.tenant,
            created_by=request.user,
        ).filter(artifact_lookup).first()
        if artifact is None:
            return Response({"detail": "다운로드 작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        job = None
        if artifact.job_id:
            job = ai_repo.get_job_model_for_status(
                artifact.job_id,
                str(request.tenant.id),
                job_type=EXPORT_JOB_TYPE,
            )
            if job and not _job_owned_by_report(job, report):
                job = None
        progress = None
        try:
            from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

            if artifact.job_id:
                progress = RedisProgressAdapter().get_progress(artifact.job_id, tenant_id=str(request.tenant.id))
        except Exception:
            pass
        artifact_payload = _serialize_artifact(artifact, include_download=True)
        response = Response({
            **artifact_payload,
            "job_id": artifact.job_id,
            "status": artifact.status,
            "progress": progress,
            "result": artifact_payload if artifact.status == ProblemReviewArtifact.Status.READY else None,
            "error_message": artifact.error_message or (job.error_message if job else None) or (job.last_error if job else None),
        })
        response["Cache-Control"] = "no-store"
        return response


class ProblemReviewPublishView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="tools_problem_review_publish",
        request=ProblemReviewPublishRequestSerializer,
        responses=ProblemReviewPublishResponseSerializer,
    )
    def post(self, request, report_id):
        try:
            expected_version = int(request.data.get("version"))
        except (AttributeError, TypeError, ValueError):
            return Response(
                {"detail": "현재 리포트 버전을 확인해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            report = ProblemReviewReport.objects.select_for_update().filter(
                pk=report_id,
                tenant=request.tenant,
                requested_by=request.user,
            ).first()
            if report is None:
                return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
            report = _refresh_analysis(report)
            if report.status != ProblemReviewReport.Status.DRAFT:
                return Response(
                    {"detail": "분석과 선생님 검수가 끝난 리포트만 공개할 수 있습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            if report.version != expected_version:
                return Response(
                    {"detail": "다른 화면에서 수정되었습니다. 최신 리포트를 다시 열어 주세요."},
                    status=status.HTTP_409_CONFLICT,
                )
            readiness = build_review_readiness(
                report.draft,
                finalized_fingerprint=report.review_fingerprint,
                finalized_at=report.review_completed_at,
            )
            if not readiness["is_finalized"]:
                return Response(
                    {
                        "detail": "전 문항 원문·정답 대조와 최종 검수 확정 뒤 공개할 수 있습니다.",
                        "review_readiness": readiness,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            snapshot = _public_snapshot(
                report.draft,
                verified_at=report.review_completed_at,
                fingerprint=report.review_fingerprint,
            )
            if not snapshot.get("questions"):
                return Response(
                    {"detail": "공개할 문항 분석이 없습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            title = str(snapshot.get("metadata", {}).get("title") or report.title or "문제 분석 리포트")[:200]
            description = str(snapshot.get("summary", {}).get("one_line") or "")[:1200]

        pdf_bytes, _, _ = render_problem_review_report(snapshot, output_format="pdf")
        snapshot_key = (
            f"problem-review-showcase-snapshots/tenant_{request.tenant.id}/"
            f"{report.id}/{uuid.uuid4().hex}.pdf"
        )
        try:
            from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

            upload_fileobj_to_r2_storage(
                fileobj=io.BytesIO(pdf_bytes),
                key=snapshot_key,
                content_type="application/pdf",
                timeout_seconds=30,
            )
        except Exception:
            logger.exception("problem_review_public_snapshot_upload_failed report=%s", report.id)
            return Response(
                {"detail": "공개용 PDF를 저장하지 못했습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        now = timezone.now()
        old_key = ""
        try:
            with transaction.atomic():
                current_report = ProblemReviewReport.objects.select_for_update().filter(
                    pk=report.id,
                    tenant=request.tenant,
                    requested_by=request.user,
                    status=ProblemReviewReport.Status.DRAFT,
                    version=expected_version,
                ).first()
                if current_report is None:
                    raise _ProblemReviewPublishConflict
                showcase = publish_problem_review_showcase(
                    tenant=request.tenant,
                    report_id=report.id,
                    title=title,
                    description=description,
                    published_at=now,
                    snapshot=snapshot,
                    snapshot_pdf_key=snapshot_key,
                    snapshot_pdf_bytes=len(pdf_bytes),
                    created_by=request.user,
                )
                old_key = showcase.previous_snapshot_pdf_key
        except _ProblemReviewPublishConflict:
            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_storage

                delete_object_r2_storage(key=snapshot_key, timeout_seconds=10)
            except Exception:
                logger.warning("problem_review_public_snapshot_cleanup_failed key=%s", snapshot_key)
            return Response(
                {"detail": "다른 화면에서 수정되었습니다. 최신 리포트를 다시 열어 주세요."},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception("problem_review_public_snapshot_publish_failed report=%s", report.id)
            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_storage

                delete_object_r2_storage(key=snapshot_key, timeout_seconds=10)
            except Exception:
                logger.warning("problem_review_public_snapshot_cleanup_failed key=%s", snapshot_key)
            return Response(
                {"detail": "홈페이지 공개본을 저장하지 못했습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if old_key and old_key != snapshot_key:
            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_storage

                delete_object_r2_storage(key=old_key, timeout_seconds=10)
            except Exception:
                logger.warning("problem_review_old_public_snapshot_cleanup_failed key=%s", old_key)

        tenant_code = request.tenant.code
        response = Response({
            "id": showcase.id,
            "title": showcase.title,
            "status": showcase.status,
            "published_at": showcase.published_at.isoformat(),
            "public_url": f"/landing/analysis/{showcase.id}",
            "pdf_url": (
                f"/api/v1/landing-public/problem-review-showcase/{showcase.id}/pdf/"
                f"?tenant={tenant_code}"
            ),
        })
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        operation_id="tools_problem_review_unpublish",
        request=None,
        responses={204: None},
    )
    def delete(self, request, report_id):
        report = _get_owned_report(request, report_id)
        if report is None:
            return Response({"detail": "리포트를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        updated = hide_problem_review_showcase(
            tenant=request.tenant,
            report_id=report.id,
        )
        if not updated:
            return Response({"detail": "공개본을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
