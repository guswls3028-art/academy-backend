from __future__ import annotations

import uuid

from django.core.cache import cache
from PIL import Image, UnidentifiedImageError
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academy.adapters.db.django import repositories_ai as ai_repo
from apps.core.permissions import TenantResolvedAndStaff
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    upload_fileobj_to_r2_storage,
)
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


JOB_TYPE = "teacher_problem_explanation"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MIN_SHORT_SIDE = 320
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
GENERIC_FAILURE_MESSAGE = "풀이 초안을 만들지 못했습니다. 사진을 확인하고 다시 시도해 주세요."


def _is_truthy(value: object) -> bool:
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_image(uploaded_file) -> tuple[str, str]:
    if not uploaded_file:
        raise ValueError("풀이할 문제 사진을 올려 주세요.")
    if int(getattr(uploaded_file, "size", 0) or 0) <= 0:
        raise ValueError("비어 있는 사진은 올릴 수 없습니다.")
    if int(uploaded_file.size) > MAX_IMAGE_BYTES:
        raise ValueError("사진은 12MB 이하로 올려 주세요.")

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if image_format not in ALLOWED_FORMATS:
                raise ValueError("JPG, PNG, WEBP 사진만 올릴 수 있습니다.")
            if min(width, height) < MIN_SHORT_SIDE:
                raise ValueError("짧은 변이 320px 이상인 선명한 사진을 올려 주세요.")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("사진 해상도가 너무 큽니다. 크기를 줄여 다시 올려 주세요.")
            image.verify()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("정상적인 JPG, PNG, WEBP 사진인지 확인해 주세요.") from exc
    finally:
        uploaded_file.seek(0)

    return ALLOWED_FORMATS[image_format]


def _job_belongs_to_request_user(job, request) -> bool:
    payload = job.payload if isinstance(job.payload, dict) else {}
    return str(payload.get("request_user_id") or "") == str(request.user.id)


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    return response


class TeacherProblemExplanationJobCreateView(APIView):
    """Create an isolated teacher-review AI explanation draft."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        if not _is_truthy(request.data.get("privacy_confirmed")):
            return Response(
                {"detail": "개인정보가 보이지 않는 문제 사진인지 확인해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subject = str(request.data.get("subject") or "").strip()
        if len(subject) > 40:
            return Response(
                {"detail": "과목은 40자 이내로 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_file = request.FILES.get("image")
        try:
            extension, content_type = _validate_image(uploaded_file)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        tenant_id = str(request.tenant.id)
        user_id = str(request.user.id)
        lock_key = f"tools:problem-solver:create:{tenant_id}:{user_id}"
        if not cache.add(lock_key, "1", timeout=10):
            return Response(
                {"detail": "이미 풀이 작업을 시작하고 있습니다. 잠시 후 다시 확인해 주세요."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        image_key = (
            f"tenants/{tenant_id}/tools/problem-solver/tmp/"
            f"{uuid.uuid4().hex}/problem.{extension}"
        )
        uploaded = False
        try:
            upload_fileobj_to_r2_storage(
                fileobj=uploaded_file,
                key=image_key,
                content_type=content_type,
            )
            uploaded = True
            result = dispatch_tools_ai_job(
                job_type=JOB_TYPE,
                payload={
                    "tenant_id": tenant_id,
                    "request_user_id": user_id,
                    "source_image_key": image_key,
                    "content_type": content_type,
                    "subject": subject,
                    "file_size_bytes": int(uploaded_file.size),
                },
                tenant_id=tenant_id,
                source_domain="tools_problem_solver",
                source_id=None,
                tier="basic",
            )
            if not result.get("ok"):
                delete_object_r2_storage(key=image_key)
                uploaded = False
                cache.delete(lock_key)
                return Response(
                    {"detail": "풀이 작업을 시작할 수 없습니다. 잠시 후 다시 시도해 주세요."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        except Exception:
            if uploaded:
                try:
                    delete_object_r2_storage(key=image_key)
                except Exception:
                    pass
            cache.delete(lock_key)
            return Response(
                {"detail": "사진을 안전하게 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return _no_store(Response(
            {"job_id": result["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        ))


class TeacherProblemExplanationJobStatusView(APIView):
    """Return only the requesting teacher's review-required draft."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(
            str(job_id),
            str(request.tenant.id),
            job_type=JOB_TYPE,
        )
        if not job or not _job_belongs_to_request_user(job, request):
            return _no_store(Response(
                {"detail": "작업을 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            ))

        public_result = None
        if job.status == "DONE":
            stored_result = (
                ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
            )
            public_result = {
                "answer": str(stored_result.get("answer") or ""),
                "explanation": str(stored_result.get("explanation") or ""),
                "answer_check": str(stored_result.get("answer_check") or ""),
                "confidence": str(stored_result.get("confidence") or "low"),
                "review_status": "teacher_review_required",
                "subject": str(stored_result.get("subject") or ""),
            }

        response_body = {
            "job_id": job.job_id,
            "status": job.status,
            "error": (
                GENERIC_FAILURE_MESSAGE
                if job.status in {
                    "FAILED",
                    "REJECTED_BAD_INPUT",
                    "FALLBACK_TO_GPU",
                    "REVIEW_REQUIRED",
                }
                else ""
            ),
            "result": public_result,
        }
        return _no_store(Response(response_body))
