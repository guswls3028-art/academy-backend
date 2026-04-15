# PATH: apps/domains/tools/timer_download_view.py
# 타이머 exe 다운로드 presigned URL 반환

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

# tenant.code → R2 key 매핑 (build_all.py TENANTS와 동일)
_TIMER_EXE_MAP: dict[str, str] = {
    "hakwonplus": "tools/timer/hakwonplus/Timer.exe",
    "tchul":      "tools/timer/tchul/Tchul.com_Timer.exe",
    "limglish":   "tools/timer/limglish/Limglish_Timer.exe",
    "ymath":      "tools/timer/ymath/Y_math_Timer.exe",
    "sswe":       "tools/timer/sswe/SSWE_Academy_Timer.exe",
    "dnb":        "tools/timer/dnb/DnB_\uBCF4\uC2B5\uD559\uC6D0_Timer.exe",
}

# 다운로드 시 사용자에게 보여줄 파일명
_TIMER_FILENAME_MAP: dict[str, str] = {
    "hakwonplus": "Timer.exe",
    "tchul":      "Tchul.com_Timer.exe",
    "limglish":   "Limglish_Timer.exe",
    "ymath":      "Y_math_Timer.exe",
    "sswe":       "SSWE_Academy_Timer.exe",
    "dnb":        "DnB_Timer.exe",
}


class TimerDownloadView(APIView):
    """GET /api/v1/tools/timer/download/ — 현재 테넌트의 타이머 exe presigned URL 반환."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        code = tenant.code

        r2_key = _TIMER_EXE_MAP.get(code)
        if not r2_key:
            return Response(
                {"detail": "이 학원에 대한 타이머 프로그램이 아직 준비되지 않았습니다."},
                status=404,
            )

        filename = _TIMER_FILENAME_MAP.get(code, "Timer.exe")
        url = generate_presigned_get_url_storage(
            key=r2_key,
            expires_in=3600,
            filename=filename,
            content_type="application/x-msdownload",
        )

        return Response({"download_url": url, "filename": filename})
