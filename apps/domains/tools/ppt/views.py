# PATH: apps/domains/tools/ppt/views.py
# PPT 생성 API — 이미지 업로드 → PPTX 생성 → R2 업로드 → presigned URL 반환

from __future__ import annotations

import io
import json
import logging
import time
import uuid

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import generate_ppt

logger = logging.getLogger(__name__)

# 허용 이미지 MIME 타입 (SVG 제외 — Pillow 미지원 + XXE 벡터)
ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
}

# 이미지 파일 매직 바이트 (실제 바이너리 헤더 검증)
IMAGE_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # RIFF____WEBP
    b"BM": "image/bmp",
    b"II": "image/tiff",  # little-endian TIFF
    b"MM": "image/tiff",  # big-endian TIFF
}

# 최대 제한
MAX_IMAGES = 50  # 50장으로 축소 (메모리 안전 + 실용적 충분)
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB per image
MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200MB total


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
        return None  # 중복이나 범위 밖 인덱스 차단
    return int_order


@method_decorator([csrf_exempt, require_POST], name="dispatch")
class PptGenerateView(View):
    """POST: 이미지 업로드 → PPT 생성 → R2 저장 → presigned download URL 반환."""

    @_jwt_required
    @_tenant_required
    def post(self, request, *args, **kwargs):
        t_start = time.monotonic()

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
                {"detail": "이미지를 업로드해주세요.", "code": "no_images"},
                status=400,
            )

        if len(images_files) > MAX_IMAGES:
            return JsonResponse(
                {"detail": f"최대 {MAX_IMAGES}장까지 업로드할 수 있습니다.", "code": "too_many_images"},
                status=400,
            )

        # MIME 타입 + 크기 + 매직 바이트 검증
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

            # 매직 바이트 검증 (첫 16바이트 읽고 되감기)
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

        # 순서 파싱
        order_str = request.POST.get("order", "")
        validated_order = None
        if order_str:
            try:
                raw_order = json.loads(order_str)
                validated_order = _validate_order(raw_order, len(images_files))
            except (json.JSONDecodeError, TypeError):
                pass  # 잘못된 순서 → 무시 (원본 순서)

        # 설정 파싱
        settings_str = request.POST.get("settings", "{}")
        try:
            ppt_settings = json.loads(settings_str)
            if not isinstance(ppt_settings, dict):
                ppt_settings = {}
        except (json.JSONDecodeError, TypeError):
            ppt_settings = {}

        # 이미지 바이트 읽기 (스트리밍으로 한장씩)
        raw_images = []
        for f in images_files:
            raw_images.append((f.name, f.read()))

        # 순서 적용 (검증된 순서만)
        if validated_order is not None:
            raw_images = [raw_images[i] for i in validated_order]

        # PPT 생성 설정 추출 및 검증
        aspect_ratio = ppt_settings.get("aspect_ratio", "16:9")
        if aspect_ratio not in ("16:9", "4:3"):
            aspect_ratio = "16:9"

        background = ppt_settings.get("background", "black")
        # background XSS/injection 방지: 허용된 이름 또는 hex만
        if background not in ("black", "white", "dark_gray"):
            if not (isinstance(background, str) and len(background) == 7 and background.startswith("#")):
                background = "black"

        fit_mode = ppt_settings.get("fit_mode", "contain")
        if fit_mode not in ("contain", "cover", "stretch"):
            fit_mode = "contain"

        invert = bool(ppt_settings.get("invert", False))
        grayscale = bool(ppt_settings.get("grayscale", False))
        auto_enhance = bool(ppt_settings.get("auto_enhance", False))

        # 밝기/대비: float, clamp 0.2~3.0
        try:
            brightness_val = float(ppt_settings.get("brightness", 1.0))
        except (ValueError, TypeError):
            brightness_val = 1.0
        try:
            contrast_val = float(ppt_settings.get("contrast", 1.0))
        except (ValueError, TypeError):
            contrast_val = 1.0

        per_slide = ppt_settings.get("per_slide")
        if per_slide is not None and not isinstance(per_slide, list):
            per_slide = None

        try:
            pptx_bytes = generate_ppt(
                raw_images,
                aspect_ratio=aspect_ratio,
                background=background,
                fit_mode=fit_mode,
                invert=invert,
                grayscale=grayscale,
                auto_enhance=auto_enhance,
                brightness=brightness_val,
                contrast=contrast_val,
                per_slide_settings=per_slide,
            )
        except ValueError as exc:
            logger.warning("PPT 생성 실패 (validation): tenant=%s user=%s: %s",
                           request.tenant.id, request.user.id, exc)
            return JsonResponse(
                {"detail": str(exc), "code": "generation_failed"},
                status=400,
            )
        except Exception:
            logger.exception("PPT 생성 실패 (unexpected): tenant=%s user=%s",
                             request.tenant.id, request.user.id)
            return JsonResponse(
                {"detail": "PPT 생성 중 오류가 발생했습니다.", "code": "generation_failed"},
                status=500,
            )

        # R2 업로드
        unique_id = uuid.uuid4().hex[:12]
        r2_key = f"tenants/{request.tenant.id}/tools/ppt/{unique_id}.pptx"
        filename = f"presentation_{unique_id}.pptx"

        try:
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
            logger.exception("R2 업로드 실패: tenant=%s key=%s", request.tenant.id, r2_key)
            return JsonResponse(
                {"detail": "파일 저장에 실패했습니다. 잠시 후 다시 시도해주세요.", "code": "storage_failed"},
                status=500,
            )

        elapsed = time.monotonic() - t_start
        logger.info(
            "PPT 생성 완료: tenant=%s user=%s slides=%d size=%d elapsed=%.1fs",
            request.tenant.id, request.user.id, len(raw_images), len(pptx_bytes), elapsed,
        )

        return JsonResponse({
            "download_url": download_url,
            "filename": filename,
            "slide_count": len(raw_images),
            "size_bytes": len(pptx_bytes),
        })
