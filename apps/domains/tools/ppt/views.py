# PATH: apps/domains/tools/ppt/views.py
# PPT 생성 API — 이미지 업로드 → PPTX 생성 → R2 업로드 → presigned URL 반환

from __future__ import annotations

import json
import logging
import uuid

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import generate_ppt

logger = logging.getLogger(__name__)

# 허용 이미지 MIME 타입
ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff", "image/svg+xml",
}

# 최대 제한
MAX_IMAGES = 100
MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB per image
MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 500MB total


def _jwt_required(view_func):
    """JWT 인증 필수."""
    def wrapped(request, *args, **kwargs):
        auth = JWTAuthentication()
        result = auth.authenticate(request)
        if result is None:
            return JsonResponse(
                {"detail": "Authentication required", "code": "auth_required"},
                status=401,
            )
        request.user, request.auth = result[0], result[1]
        return view_func(request, *args, **kwargs)
    return wrapped


def _tenant_required(view_func):
    """테넌트 필수."""
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped


def _is_tenant_staff(request) -> bool:
    """요청 사용자가 테넌트의 스태프인지 확인."""
    from apps.core.models import Membership
    return Membership.objects.filter(
        user=request.user,
        tenant=request.tenant,
        is_active=True,
    ).exists()


@method_decorator(csrf_exempt, name="dispatch")
class PptGenerateView(View):
    """POST: 이미지 업로드 → PPT 생성 → R2 저장 → presigned download URL 반환.

    Request (multipart/form-data):
      - images: 이미지 파일들 (순서대로 슬라이드)
      - order: JSON 배열 — 이미지 인덱스 순서 (예: [2,0,1])
      - settings: JSON 객체 — PPT 생성 옵션
        {
          "aspect_ratio": "16:9" | "4:3",
          "background": "black" | "white" | "dark_gray" | "#RRGGBB",
          "fit_mode": "contain" | "cover" | "stretch",
          "invert": true | false,
          "grayscale": true | false,
          "per_slide": [{"invert": true}, ...]
        }

    Response:
      {
        "download_url": "https://...",
        "filename": "presentation_xxx.pptx",
        "slide_count": 5,
        "size_bytes": 12345
      }
    """

    @_jwt_required
    @_tenant_required
    def post(self, request, *args, **kwargs):
        # 스태프 확인
        if not _is_tenant_staff(request):
            return JsonResponse(
                {"detail": "Staff permission required"},
                status=403,
            )

        # 이미지 파일 추출
        images_files = request.FILES.getlist("images")
        if not images_files:
            return JsonResponse(
                {"detail": "No images provided", "code": "no_images"},
                status=400,
            )

        if len(images_files) > MAX_IMAGES:
            return JsonResponse(
                {"detail": f"Maximum {MAX_IMAGES} images allowed", "code": "too_many_images"},
                status=400,
            )

        # MIME 타입 검증 + 크기 검증
        total_size = 0
        for f in images_files:
            ct = (f.content_type or "").lower()
            if ct not in ALLOWED_IMAGE_TYPES:
                return JsonResponse(
                    {"detail": f"Unsupported image type: {ct}", "code": "invalid_type"},
                    status=400,
                )
            if f.size > MAX_IMAGE_SIZE:
                return JsonResponse(
                    {"detail": f"Image too large: {f.name} ({f.size} bytes)", "code": "image_too_large"},
                    status=400,
                )
            total_size += f.size

        if total_size > MAX_TOTAL_SIZE:
            return JsonResponse(
                {"detail": "Total upload size exceeds limit", "code": "total_too_large"},
                status=400,
            )

        # 순서 파싱
        order_str = request.POST.get("order", "")
        order = None
        if order_str:
            try:
                order = json.loads(order_str)
                if not isinstance(order, list):
                    order = None
            except (json.JSONDecodeError, TypeError):
                order = None

        # 설정 파싱
        settings_str = request.POST.get("settings", "{}")
        try:
            ppt_settings = json.loads(settings_str)
        except (json.JSONDecodeError, TypeError):
            ppt_settings = {}

        # 이미지 바이트 읽기
        raw_images = []
        for f in images_files:
            raw_images.append((f.name, f.read()))

        # 순서 적용
        if order and len(order) == len(raw_images):
            try:
                ordered = [raw_images[i] for i in order]
                raw_images = ordered
            except (IndexError, TypeError):
                pass  # 순서 오류 시 원본 순서 유지

        # PPT 생성
        aspect_ratio = ppt_settings.get("aspect_ratio", "16:9")
        if aspect_ratio not in ("16:9", "4:3"):
            aspect_ratio = "16:9"

        background = ppt_settings.get("background", "black")
        fit_mode = ppt_settings.get("fit_mode", "contain")
        if fit_mode not in ("contain", "cover", "stretch"):
            fit_mode = "contain"

        invert = bool(ppt_settings.get("invert", False))
        grayscale = bool(ppt_settings.get("grayscale", False))
        per_slide = ppt_settings.get("per_slide")

        try:
            pptx_bytes = generate_ppt(
                raw_images,
                aspect_ratio=aspect_ratio,
                background=background,
                fit_mode=fit_mode,
                invert=invert,
                grayscale=grayscale,
                per_slide_settings=per_slide,
            )
        except Exception:
            logger.exception("PPT 생성 실패 (tenant=%s, user=%s)", request.tenant.id, request.user.id)
            return JsonResponse(
                {"detail": "PPT generation failed", "code": "generation_failed"},
                status=500,
            )

        # R2 업로드
        unique_id = uuid.uuid4().hex[:12]
        r2_key = f"tenants/{request.tenant.id}/tools/ppt/{unique_id}.pptx"
        filename = f"presentation_{unique_id}.pptx"

        try:
            import io
            from apps.infrastructure.storage.r2 import (
                upload_fileobj_to_r2_storage,
                generate_presigned_get_url_storage,
            )
            upload_fileobj_to_r2_storage(
                fileobj=io.BytesIO(pptx_bytes),
                key=r2_key,
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
            download_url = generate_presigned_get_url_storage(
                key=r2_key,
                expires_in=3600,
                filename=filename,
                content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        except Exception:
            logger.exception("R2 업로드 실패 (tenant=%s)", request.tenant.id)
            return JsonResponse(
                {"detail": "File storage failed", "code": "storage_failed"},
                status=500,
            )

        return JsonResponse({
            "download_url": download_url,
            "filename": filename,
            "slide_count": len(raw_images),
            "size_bytes": len(pptx_bytes),
        })
