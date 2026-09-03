from pathlib import Path

from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.tools.timer_download_view import TimerDownloadView


def test_legacy_timer_download_fails_closed_without_storage_url():
    response = TimerDownloadView().get(request=None)

    assert response.status_code == 410
    assert response.data == {
        "code": "trusted_timer_distribution_required",
        "detail": (
            "서명되지 않은 Windows 타이머 배포를 중단했습니다. "
            "도구의 웹 타이머를 사용해 주세요."
        ),
        "distribution": "web_pwa",
        "web_path": "/workspace/tools/stopwatch",
    }


def test_legacy_timer_download_keeps_staff_and_tenant_permissions():
    assert TimerDownloadView.permission_classes == [
        IsAuthenticated,
        TenantResolvedAndStaff,
    ]


def test_legacy_timer_endpoint_has_no_presigned_storage_path():
    source = Path(
        "apps/domains/tools/timer_download_view.py"
    ).read_text(encoding="utf-8")

    assert "generate_presigned_get_url" not in source
    assert "timer_tenants.json" not in source
