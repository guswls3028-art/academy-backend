from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from academy.adapters.db.django import repositories_ai as ai_repo
from apps.domains.ai.gateway import dispatch_job
from apps.domains.tools.problem_studio.services import extract_sources, parse_payload, source_extraction_to_payload
from apps.domains.tools.problem_studio.async_transfer import build_source_archive
from apps.domains.tools.problem_studio.transfer_documents import (
    build_transfer_package,
    package_to_response,
)


class ProblemStudioTransferDocumentView(APIView):
    """POST /api/v1/tools/problem-studio/transfer-document/

    원본 파일을 AI 생성 없이 한글/워드 호환 검수 문서 패키지로 이관한다.
    큰 PDF/HWP/ZIP 산출물은 JSON/AI 워커 payload를 거치지 않고 바로 파일로
    내려보내 용량 폭발을 피한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            package = build_transfer_package(
                payload=payload,
                source_files=request.FILES.getlist("source_files"),
            )
            return package_to_response(package)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProblemStudioTransferJobCreateView(APIView):
    """POST /api/v1/tools/problem-studio/transfer-jobs/

    대용량 원본 이관은 API/ALB 60초 경계를 넘을 수 있으므로 R2 임시 소스
    아카이브 + tools worker로 처리한다. 완료 결과는 generic job status
    endpoint의 result.download_url로 내려간다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        archive_file = None
        archive_key = ""
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            source_files = request.FILES.getlist("source_files")
            if not source_files:
                return Response({"detail": "원본으로 옮길 소스 파일을 먼저 올려 주세요."}, status=status.HTTP_400_BAD_REQUEST)

            archive_file, source_manifest = build_source_archive(source_files)

            import uuid
            from apps.infrastructure.storage.r2 import delete_object_r2_storage, upload_fileobj_to_r2_storage

            tenant_id = str(request.tenant.id)
            unique = uuid.uuid4().hex[:12]
            archive_key = f"tenants/{tenant_id}/tools/problem-studio/tmp/{unique}/sources.zip"
            upload_fileobj_to_r2_storage(
                fileobj=archive_file,
                key=archive_key,
                content_type="application/zip",
            )

            result = dispatch_job(
                job_type="problem_studio_transfer",
                payload={
                    "problem_studio_payload": payload,
                    "source_archive_key": archive_key,
                    "source_files": source_manifest,
                    "tenant_id": tenant_id,
                },
                tenant_id=tenant_id,
                source_domain="tools_problem_studio",
                source_id=None,
                tier="basic",
            )
            if not result.get("ok"):
                if archive_key:
                    try:
                        delete_object_r2_storage(key=archive_key)
                    except Exception:
                        pass
                return Response(
                    {
                        "detail": result.get("error") or "원본 이관 작업을 시작할 수 없습니다.",
                        "rejection_code": result.get("rejection_code"),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {
                    "job_id": result["job_id"],
                    "status": "PENDING",
                    "source_files": [
                        {
                            "name": item["name"],
                            "kind": item["name"].rsplit(".", 1)[-1].upper() if "." in item["name"] else "기타",
                            "sizeLabel": f"{item['size'] / (1024 * 1024):.1f}MB" if item["size"] >= 1024 * 1024 else f"{item['size'] / 1024:.1f}KB",
                            "extractedChars": 0,
                            "warning": None,
                        }
                        for item in source_manifest
                    ],
                    "warnings": [],
                    "source_text_chars": 0,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if archive_file is not None:
                archive_file.close()


class ProblemStudioJobCreateView(APIView):
    """POST /api/v1/tools/problem-studio/jobs/

    문항 생성처럼 오래 걸릴 수 있는 처리는 AI-SQS 워커로 넘긴다. 업로드 파일
    본문 추출은 API에서 한 번만 수행하고, 추출된 텍스트와 메타를 worker payload로
    전달해 request 파일 수명에 의존하지 않게 한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            sources = extract_sources(request.FILES.getlist("source_files"))
            source_payloads = [source_extraction_to_payload(source) for source in sources]
            result = dispatch_job(
                job_type="problem_studio_package",
                payload={
                    "problem_studio_payload": payload,
                    "source_files": source_payloads,
                    "tenant_id": str(request.tenant.id),
                },
                tenant_id=str(request.tenant.id),
                source_domain="tools_problem_studio",
                source_id=None,
                tier="basic",
            )
            if not result.get("ok"):
                return Response(
                    {
                        "detail": result.get("error") or "문항 생성 작업을 시작할 수 없습니다.",
                        "rejection_code": result.get("rejection_code"),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {
                    "job_id": result["job_id"],
                    "status": "PENDING",
                    "source_files": [
                        {
                            "name": source.name,
                            "kind": source.kind,
                            "sizeLabel": source.size_label,
                            "extractedChars": len(source.extracted_text),
                            "warning": source.warning,
                        }
                        for source in sources
                    ],
                    "warnings": [source.warning for source in sources if source.warning],
                    "source_text_chars": sum(len(source.extracted_text) for source in sources),
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProblemStudioJobStatusView(APIView):
    """GET /api/v1/tools/problem-studio/jobs/<job_id>/"""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(
            str(job_id),
            str(request.tenant.id),
            job_type="problem_studio_package",
        )
        if not job:
            return Response({"detail": "작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) if job.status == "DONE" else None
        return Response({
            "job_id": job.job_id,
            "status": job.status,
            "error": job.error_message or job.last_error or "",
            "result": result_payload,
        })
