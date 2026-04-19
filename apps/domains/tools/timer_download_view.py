# PATH: apps/domains/tools/timer_download_view.py
# 타이머 프로그램 다운로드 presigned URL 반환.
# 테넌트 매핑은 timer_tenants.json (SSOT) 한 파일에서 읽는다.
# exe는 Windows SmartScreen / 브라우저 필터에 자주 차단되므로 ZIP으로 배포.

import json
import os
from functools import lru_cache

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

_SSOT_PATH = os.path.join(os.path.dirname(__file__), "timer_tenants.json")


@lru_cache(maxsize=1)
def _load_tenant_index() -> dict[str, dict]:
    """timer_tenants.json 로드 → tenant_code → config dict 인덱스."""
    with open(_SSOT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {t["tenant_code"]: t for t in data["tenants"]}


class TimerDownloadView(APIView):
    """GET /api/v1/tools/timer/download/ — 현재 테넌트의 타이머 ZIP presigned URL 반환."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        code = tenant.code

        cfg = _load_tenant_index().get(code)
        if cfg is None:
            return Response(
                {"detail": "이 학원에 대한 타이머 프로그램이 아직 준비되지 않았습니다."},
                status=404,
            )

        r2_key = f"tools/timer/{code}/{cfg['r2_zip_filename']}"
        download_filename = cfg["download_filename"]

        url = generate_presigned_get_url_storage(
            key=r2_key,
            expires_in=3600,
            filename=download_filename,
            content_type="application/zip",
        )

        return Response({"download_url": url, "filename": download_filename})
