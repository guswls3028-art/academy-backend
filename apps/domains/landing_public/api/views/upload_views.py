"""수강후기 사진 R2 업로드 — Phase 4-D.

POST /api/v1/landing-public/uploads/review-photo/
- multipart/form-data: file (이미지) + (optional) review_id (기존 후기에 추가 첨부)
- 권한: TenantResolvedAndMember (학원 family — 학생/학부모/staff)
- R2 키: `landing-public/reviews/{tenant_id}/{ulid}.{ext}`
- 응답: { key, url } → frontend가 photos 배열에 URL 추가하여 후기 update.
"""
import logging
import secrets
from rest_framework import status, views
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember

logger = logging.getLogger(__name__)


_ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
_MAX_SIZE = 6 * 1024 * 1024  # 6MB — landing image 5MB보다 약간 여유


class ReviewPhotoUploadView(views.APIView):
    """후기 사진 업로드. tenant 격리 + family 권한 + 매직바이트 검증."""

    permission_classes = [TenantResolvedAndMember]

    def post(self, request):
        tenant = request.tenant
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file은 필수입니다."}, status=status.HTTP_400_BAD_REQUEST)
        if not (getattr(file, "content_type", "") or "").startswith("image/"):
            return Response({"detail": "이미지 파일만 업로드 가능합니다."}, status=status.HTTP_400_BAD_REQUEST)
        if file.size > _MAX_SIZE:
            return Response({"detail": "이미지 크기는 6MB 이하여야 합니다."}, status=status.HTTP_400_BAD_REQUEST)
        ext = (file.name or "").rsplit(".", 1)[-1].lower()
        if ext not in _ALLOWED_EXT:
            return Response({"detail": "PNG / JPG / WebP만 허용됩니다."}, status=status.HTTP_400_BAD_REQUEST)
        # 매직바이트 검증 (Content-Type 위장 차단)
        try:
            from apps.api.common.image_validator import is_real_image
            if not is_real_image(file):
                return Response({"detail": "이미지 파일이 손상되었거나 이미지 형식이 아닙니다."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.warning("REVIEW_PHOTO_MAGIC_BYTE_CHECK_UNAVAILABLE")

        # 키 생성 — 충돌 방지용 8byte 랜덤 토큰
        token = secrets.token_urlsafe(8).replace("=", "").replace("-", "").replace("_", "")[:12]
        key = f"landing-public/reviews/{tenant.id}/{token}.{ext}"

        try:
            from apps.infrastructure.storage import r2 as r2_storage
            r2_storage.upload_fileobj_to_r2_admin(
                fileobj=file,
                key=key,
                content_type=file.content_type or "image/png",
            )
            url = r2_storage.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7)
        except Exception as e:
            logger.exception("REVIEW_PHOTO_UPLOAD_FAIL | tenant=%s err=%s", tenant.id, e)
            return Response({"detail": "업로드 실패. 잠시 후 다시 시도해주세요."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"key": key, "url": url}, status=status.HTTP_201_CREATED)
