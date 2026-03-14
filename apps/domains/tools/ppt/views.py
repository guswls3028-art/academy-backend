# PATH: apps/domains/tools/ppt/views.py
# PPT 생성 API — async worker migration
#
# Flow:
# 1. Validate auth, tenant, staff, files
# 2. Upload images to R2 temp location
# 3. Dispatch "ppt_generation" job to AI worker
# 4. Return {job_id, status: "PENDING"}
#
# Frontend polls GET /api/v1/jobs/{job_id}/progress/ for status.
# Worker generates PPT, uploads to R2, sets result with download_url.

from __future__ import annotations

import io
import json
import logging
import uuid

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

logger = logging.getLogger(__name__)

# 허용 이미지 MIME 타입 (SVG 제외 — Pillow 미지원 + XXE 벡터)
ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
}

# PDF MIME types (for pdf mode)
ALLOWED_PDF_TYPES = {
    "application/pdf",
}

# 이미지 파일 매직 바이트 (실제 바이너리 헤더 검증)
IMAGE_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",
    b"BM": "image/bmp",
    b"II": "image/tiff",
    b"MM": "image/tiff",
}

PDF_MAGIC = b"%PDF"

# 최대 제한
MAX_IMAGES = 50
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB per image
MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200MB total
MAX_PDF_SIZE = 100 * 1024 * 1024  # 100MB per PDF


def _is_tenant_staff(request) -> bool:
    """요청 사용자가 테넌트의 스태프인지 확인."""
    from apps.core.models.tenant_membership import TenantMembership
    return TenantMembership.objects.filter(
        user=request.user,
        tenant=request.tenant,
        is_active=True,
    ).exists()


def _verify_image_magic(data: bytes) -> bool:
    """파일 매직 바이트로 실제 이미지인지 검증."""
    for magic in IMAGE_MAGIC_BYTES:
        if data[:len(magic)] == magic:
            return True
    return False


def _validate_order(order: list, image_count: int) -> list[int] | None:
    """order 배열 검증: 유효한 인덱스, 중복 없음, 전수 포함."""
    if not isinstance(order, list):
        return None
    if len(order) != image_count:
        return None
    try:
        int_order = [int(i) for i in order]
    except (ValueError, TypeError):
        return None
    if sorted(int_order) != list(range(image_count)):
        return None
    return int_order


@method_decorator([csrf_exempt, require_POST], name="dispatch")
class PptGenerateView(View):
    """POST: 이미지/PDF 업로드 → R2 임시 저장 → 워커 job 발행 → job_id 반환."""

    def post(self, request, *args, **kwargs):
        # ── JWT 인증 ──
        try:
            auth = JWTAuthentication()
            result = auth.authenticate(request)
            if result is None:
                return JsonResponse(
                    {"detail": "Authentication required", "code": "auth_required"},
                    status=401,
                )
            request.user, request.auth = result[0], result[1]
        except (InvalidToken, TokenError):
            return JsonResponse(
                {"detail": "Invalid or expired token", "code": "invalid_token"},
                status=401,
            )
        except Exception:
            logger.exception("JWT 인증 예외: tenant=%s", getattr(request, "tenant", None))
            return JsonResponse(
                {"detail": "Authentication failed", "code": "auth_error"},
                status=401,
            )

        # ── 테넌트 필수 ──
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)

        # ── 스태프 확인 ──
        if not _is_tenant_staff(request):
            return JsonResponse(
                {"detail": "Staff permission required"},
                status=403,
            )

        tenant_id = str(request.tenant.id)

        # ── 모드 판별: PDF or Images ──
        pdf_file = request.FILES.get("pdf")
        images_files = request.FILES.getlist("images")

        if pdf_file:
            return self._handle_pdf_mode(request, pdf_file, tenant_id)
        elif images_files:
            return self._handle_images_mode(request, images_files, tenant_id)
        else:
            return JsonResponse(
                {"detail": "이미지 또는 PDF를 업로드해주세요.", "code": "no_files"},
                status=400,
            )

    def _handle_images_mode(self, request, images_files, tenant_id: str) -> JsonResponse:
        """Images mode: upload images to R2, dispatch job."""
        if len(images_files) > MAX_IMAGES:
            return JsonResponse(
                {"detail": f"최대 {MAX_IMAGES}장까지 업로드할 수 있습니다.", "code": "too_many_images"},
                status=400,
            )

        # MIME + size + magic byte validation
        total_size = 0
        for f in images_files:
            ct = (f.content_type or "").lower()
            if ct not in ALLOWED_IMAGE_TYPES:
                return JsonResponse(
                    {"detail": f"지원하지 않는 이미지 형식입니다: {f.name}", "code": "invalid_type"},
                    status=400,
                )
            if f.size > MAX_IMAGE_SIZE:
                return JsonResponse(
                    {"detail": f"이미지가 너무 큽니다: {f.name} ({f.size // (1024*1024)}MB, 최대 20MB)", "code": "image_too_large"},
                    status=400,
                )
            total_size += f.size

            header = f.read(16)
            f.seek(0)
            if not _verify_image_magic(header):
                return JsonResponse(
                    {"detail": f"유효한 이미지 파일이 아닙니다: {f.name}", "code": "invalid_image"},
                    status=400,
                )

        if total_size > MAX_TOTAL_SIZE:
            return JsonResponse(
                {"detail": f"전체 업로드 크기가 제한을 초과합니다 (최대 {MAX_TOTAL_SIZE // (1024*1024)}MB).", "code": "total_too_large"},
                status=400,
            )

        # Parse order
        order_str = request.POST.get("order", "")
        validated_order = None
        if order_str:
            try:
                raw_order = json.loads(order_str)
                validated_order = _validate_order(raw_order, len(images_files))
            except (json.JSONDecodeError, TypeError):
                pass

        # Parse settings
        settings_str = request.POST.get("settings", "{}")
        try:
            ppt_settings = json.loads(settings_str)
            if not isinstance(ppt_settings, dict):
                ppt_settings = {}
        except (json.JSONDecodeError, TypeError):
            ppt_settings = {}

        # Apply order
        ordered_files = list(images_files)
        if validated_order is not None:
            ordered_files = [images_files[i] for i in validated_order]

        # Extract and validate PPT config
        aspect_ratio = ppt_settings.get("aspect_ratio", "16:9")
        if aspect_ratio not in ("16:9", "4:3"):
            aspect_ratio = "16:9"

        background = ppt_settings.get("background", "black")
        if background not in ("black", "white", "dark_gray"):
            if not (isinstance(background, str) and len(background) == 7 and background.startswith("#")):
                background = "black"

        fit_mode = ppt_settings.get("fit_mode", "contain")
        if fit_mode not in ("contain", "cover", "stretch"):
            fit_mode = "contain"

        # Upload each image to R2 temp location
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

        job_unique = uuid.uuid4().hex[:12]
        r2_keys = []

        for idx, f in enumerate(ordered_files):
            ext = f.name.rsplit(".", 1)[-1] if "." in f.name else "bin"
            tmp_key = f"tenants/{tenant_id}/tools/ppt/tmp/{job_unique}/{idx}.{ext}"

            upload_fileobj_to_r2_storage(
                fileobj=f,
                key=tmp_key,
                content_type=f.content_type or "application/octet-stream",
            )
            r2_keys.append(tmp_key)

        # Build settings payload for worker
        image_settings = {
            "invert": bool(ppt_settings.get("invert", False)),
            "grayscale": bool(ppt_settings.get("grayscale", False)),
            "auto_enhance": bool(ppt_settings.get("auto_enhance", False)),
            "brightness": float(ppt_settings.get("brightness", 1.0)),
            "contrast": float(ppt_settings.get("contrast", 1.0)),
        }
        per_slide = ppt_settings.get("per_slide")
        if isinstance(per_slide, list):
            image_settings["per_slide"] = per_slide

        # Dispatch job
        from apps.domains.ai.gateway import dispatch_job

        result = dispatch_job(
            job_type="ppt_generation",
            payload={
                "mode": "images",
                "r2_keys": r2_keys,
                "config": {
                    "aspect_ratio": aspect_ratio,
                    "background": background,
                    "fit_mode": fit_mode,
                },
                "settings": image_settings,
                "tenant_id": tenant_id,
            },
            tenant_id=tenant_id,
            source_domain="tools",
        )

        if not result.get("ok"):
            return JsonResponse(
                {"detail": result.get("error", "작업 등록 실패"), "code": "dispatch_failed"},
                status=500,
            )

        logger.info(
            "PPT 작업 등록: tenant=%s user=%s job_id=%s images=%d",
            tenant_id, request.user.id, result["job_id"], len(r2_keys),
        )

        return JsonResponse({
            "job_id": result["job_id"],
            "status": "PENDING",
            "slide_count": len(r2_keys),
        })

    def _handle_pdf_mode(self, request, pdf_file, tenant_id: str) -> JsonResponse:
        """PDF mode: upload PDF to R2, dispatch job with question splitting."""
        ct = (pdf_file.content_type or "").lower()
        if ct not in ALLOWED_PDF_TYPES:
            return JsonResponse(
                {"detail": "PDF 파일만 업로드할 수 있습니다.", "code": "invalid_type"},
                status=400,
            )

        if pdf_file.size > MAX_PDF_SIZE:
            return JsonResponse(
                {"detail": f"PDF가 너무 큽니다 ({pdf_file.size // (1024*1024)}MB, 최대 100MB).", "code": "pdf_too_large"},
                status=400,
            )

        # Magic byte verification
        header = pdf_file.read(16)
        pdf_file.seek(0)
        if not header.startswith(PDF_MAGIC):
            return JsonResponse(
                {"detail": "유효한 PDF 파일이 아닙니다.", "code": "invalid_pdf"},
                status=400,
            )

        # Parse settings
        settings_str = request.POST.get("settings", "{}")
        try:
            ppt_settings = json.loads(settings_str)
            if not isinstance(ppt_settings, dict):
                ppt_settings = {}
        except (json.JSONDecodeError, TypeError):
            ppt_settings = {}

        aspect_ratio = ppt_settings.get("aspect_ratio", "16:9")
        if aspect_ratio not in ("16:9", "4:3"):
            aspect_ratio = "16:9"

        background = ppt_settings.get("background", "black")
        if background not in ("black", "white", "dark_gray"):
            if not (isinstance(background, str) and len(background) == 7 and background.startswith("#")):
                background = "black"

        fit_mode = ppt_settings.get("fit_mode", "contain")
        if fit_mode not in ("contain", "cover", "stretch"):
            fit_mode = "contain"

        # Upload PDF to R2
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

        job_unique = uuid.uuid4().hex[:12]
        tmp_key = f"tenants/{tenant_id}/tools/ppt/tmp/{job_unique}/source.pdf"

        upload_fileobj_to_r2_storage(
            fileobj=pdf_file,
            key=tmp_key,
            content_type="application/pdf",
        )

        # Dispatch job
        from apps.domains.ai.gateway import dispatch_job

        result = dispatch_job(
            job_type="ppt_generation",
            payload={
                "mode": "pdf",
                "r2_key": tmp_key,
                "config": {
                    "aspect_ratio": aspect_ratio,
                    "background": background,
                    "fit_mode": fit_mode,
                },
                "tenant_id": tenant_id,
            },
            tenant_id=tenant_id,
            source_domain="tools",
        )

        if not result.get("ok"):
            return JsonResponse(
                {"detail": result.get("error", "작업 등록 실패"), "code": "dispatch_failed"},
                status=500,
            )

        logger.info(
            "PPT(PDF) 작업 등록: tenant=%s user=%s job_id=%s",
            tenant_id, request.user.id, result["job_id"],
        )

        return JsonResponse({
            "job_id": result["job_id"],
            "status": "PENDING",
        })
