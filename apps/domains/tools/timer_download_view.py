# PATH: apps/domains/tools/timer_download_view.py
# 타이머 프로그램 다운로드 presigned URL 반환
# exe는 Windows SmartScreen / 브라우저 필터에 자주 차단되므로 ZIP으로 배포.

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

# tenant.code → R2 key 매핑 (upload_to_r2.py TENANTS와 동일)
_TIMER_ZIP_MAP: dict[str, str] = {
    "hakwonplus": "tools/timer/hakwonplus/Timer.zip",
    "tchul":      "tools/timer/tchul/Tchul.com_Timer.zip",
    "limglish":   "tools/timer/limglish/Limglish_Timer.zip",
    "ymath":      "tools/timer/ymath/Y_math_Timer.zip",
    "sswe":       "tools/timer/sswe/SSWE_Academy_Timer.zip",
    "dnb":        "tools/timer/dnb/DnB_\uBCF4\uC2B5\uD559\uC6D0_Timer.zip",
}

# 다운로드 시 사용자에게 보여줄 파일명
_TIMER_FILENAME_MAP: dict[str, str] = {
    "hakwonplus": "Timer.zip",
    "tchul":      "Tchul.com_Timer.zip",
    "limglish":   "Limglish_Timer.zip",
    "ymath":      "Y_math_Timer.zip",
    "sswe":       "SSWE_Academy_Timer.zip",
    "dnb":        "DnB_Timer.zip",
}


class TimerDownloadView(APIView):
    """GET /api/v1/tools/timer/download/ — 현재 테넌트의 타이머 ZIP presigned URL 반환."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        code = tenant.code

        r2_key = _TIMER_ZIP_MAP.get(code)
        if not r2_key:
            return Response(
                {"detail": "이 학원에 대한 타이머 프로그램이 아직 준비되지 않았습니다."},
                status=404,
            )

        filename = _TIMER_FILENAME_MAP.get(code, "Timer.zip")
        url = generate_presigned_get_url_storage(
            key=r2_key,
            expires_in=3600,
            filename=filename,
            content_type="application/zip",
        )

        return Response({"download_url": url, "filename": filename})
