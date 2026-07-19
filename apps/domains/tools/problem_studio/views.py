from __future__ import annotations

import json
import logging
import os
import secrets
from functools import lru_cache
from urllib.parse import quote

from django.core.cache import cache
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from academy.adapters.db.django import repositories_ai as ai_repo
from apps.domains.tools.problem_studio.services import extract_sources, parse_payload, source_extraction_to_payload
from apps.domains.tools.problem_studio.async_transfer import build_source_archive
from apps.domains.tools.problem_studio.transfer_documents import (
    build_transfer_package,
    package_to_response,
)
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


_HANGUL_COMPANION_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "hangul_companion_manifest.json",
)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_hangul_companion_manifest() -> dict[str, str | int]:
    with open(_HANGUL_COMPANION_MANIFEST_PATH, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    required = {"version", "r2_key", "filename", "sha256", "size_bytes"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise RuntimeError("Problem Studio 한글 연결 프로그램 배포 정보가 올바르지 않습니다.")
    sha256 = str(manifest["sha256"]).lower()
    if (
        len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or int(manifest["size_bytes"]) <= 0
    ):
        raise RuntimeError("Problem Studio 한글 연결 프로그램 무결성 정보가 올바르지 않습니다.")
    return manifest


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

            ai_transcription = bool(payload.get("ai_transcription", True))
            result = dispatch_tools_ai_job(
                job_type=("problem_studio_transcription" if ai_transcription else "problem_studio_transfer"),
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
            result = dispatch_tools_ai_job(
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


class ProblemStudioTransferJobStatusView(APIView):
    """Staff-only transfer status with a freshly issued result URL."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    _JOB_TYPES = {"problem_studio_transfer", "problem_studio_transcription"}

    def get(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(str(job_id), str(request.tenant.id))
        if not job or job.job_type not in self._JOB_TYPES:
            return Response({"detail": "작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        progress = None
        try:
            from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

            progress = RedisProgressAdapter().get_progress(str(job.job_id), tenant_id=str(request.tenant.id))
        except Exception:
            pass

        result_payload = None
        if job.status == "DONE":
            raw_result = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
            result_key = str(raw_result.get("r2_key") or "")
            expected_prefix = f"tenants/{request.tenant.id}/tools/problem-studio/"
            if result_key.startswith(expected_prefix):
                from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

                result_payload = {
                    key: value
                    for key, value in raw_result.items()
                    if key not in {"r2_key", "download_url"}
                }
                result_payload["download_url"] = generate_presigned_get_url_storage(
                    key=result_key,
                    expires_in=900,
                    filename=str(raw_result.get("filename") or "problem-studio.zip"),
                    content_type="application/zip",
                )

        return Response({
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": progress,
            "result": result_payload,
            "error_message": job.error_message or job.last_error or None,
        })


class ProblemStudioHangulCompanionDownloadView(APIView):
    """Return a staff-only URL for the sealed Windows companion package."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        from academy.adapters.storage import r2_objects

        manifest = _load_hangul_companion_manifest()
        expected_size = int(manifest["size_bytes"])
        expected_sha256 = str(manifest["sha256"]).lower()
        try:
            integrity = r2_objects.head_storage_object_integrity(key=str(manifest["r2_key"]))
        except Exception:
            logger.exception("Problem Studio Hangul companion object HEAD failed")
            integrity = None
        if integrity != (expected_size, expected_sha256):
            return Response(
                {"detail": "한글 연결 프로그램 배포본을 확인하는 중입니다. 잠시 뒤 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = Response({
            "download_url": r2_objects.create_storage_download_url(
                key=str(manifest["r2_key"]),
                filename=str(manifest["filename"]),
                content_type="application/zip",
                expires_in=600,
            ),
            "filename": str(manifest["filename"]),
            "version": str(manifest["version"]),
            "sha256": expected_sha256,
            "size_bytes": expected_size,
        })
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioHangulHandoffCreateView(APIView):
    """Create a short-lived one-time handoff for the Windows companion."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(str(job_id), str(request.tenant.id))
        if not job or job.job_type not in ProblemStudioTransferJobStatusView._JOB_TYPES or job.status != "DONE":
            return Response({"detail": "완료된 검수본을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
        result_key = str(result_payload.get("r2_key") or "")
        expected_prefix = f"tenants/{request.tenant.id}/tools/problem-studio/"
        if not result_key.startswith(expected_prefix):
            return Response({"detail": "검수본 저장 경로가 올바르지 않습니다."}, status=status.HTTP_409_CONFLICT)

        token = secrets.token_urlsafe(32)
        cache.set(
            f"problem-studio:hangul-handoff:{token}",
            {
                "job_id": str(job.job_id),
                "tenant_id": str(request.tenant.id),
                "user_id": str(request.user.id),
            },
            timeout=300,
        )
        handoff_url = request.build_absolute_uri(
            f"/api/v1/tools/problem-studio/hangul-handoffs/{token}/"
        )
        response = Response({
            "protocol_url": f"academy-hangul://insert?handoff={quote(handoff_url, safe='')}",
            "expires_in": 300,
        })
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioHangulHandoffConsumeView(APIView):
    """Consume a handoff once and return a fresh, tenant-scoped download URL."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, token: str):
        if len(token) < 32 or len(token) > 80:
            return Response({"detail": "연결 코드가 올바르지 않습니다."}, status=status.HTTP_404_NOT_FOUND)
        key = f"problem-studio:hangul-handoff:{token}"
        lock_key = f"{key}:lock"
        if not cache.add(lock_key, "1", timeout=30):
            return Response({"detail": "이미 사용 중인 연결 코드입니다."}, status=status.HTTP_409_CONFLICT)
        try:
            handoff = cache.get(key)
            cache.delete(key)
            if not isinstance(handoff, dict):
                return Response({"detail": "만료되었거나 사용된 연결 코드입니다."}, status=status.HTTP_404_NOT_FOUND)
            tenant_id = str(handoff.get("tenant_id") or "")
            job = ai_repo.get_job_model_for_status(str(handoff.get("job_id") or ""), tenant_id)
            if not job or job.status != "DONE" or job.job_type not in ProblemStudioTransferJobStatusView._JOB_TYPES:
                return Response({"detail": "검수본을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
            result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
            result_key = str(result_payload.get("r2_key") or "")
            if not result_key.startswith(f"tenants/{tenant_id}/tools/problem-studio/"):
                return Response({"detail": "검수본 저장 경로가 올바르지 않습니다."}, status=status.HTTP_409_CONFLICT)

            from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

            filename = str(result_payload.get("filename") or "problem-studio.zip")
            response = Response({
                "download_url": generate_presigned_get_url_storage(
                    key=result_key,
                    expires_in=300,
                    filename=filename,
                    content_type="application/zip",
                ),
                "filename": filename,
                "sha256": str(result_payload.get("sha256") or ""),
                "size_bytes": int(result_payload.get("size_bytes") or 0),
            })
            response["Cache-Control"] = "no-store"
            return response
        finally:
            cache.delete(lock_key)
