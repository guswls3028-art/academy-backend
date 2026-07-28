import shutil
import subprocess
from pathlib import Path

import pytest

from apps.domains.video.management.commands.check_api_env_settings import _mask


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "base.py"
PROD_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "prod.py"
SYNC_ENV = REPO_ROOT / "scripts" / "v1" / "core" / "sync_env.ps1"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_video_cdn_runtime_is_fail_closed() -> None:
    base_settings = BASE_SETTINGS.read_text(encoding="utf-8")
    prod_settings = PROD_SETTINGS.read_text(encoding="utf-8")
    sync_env = SYNC_ENV.read_text(encoding="utf-8-sig")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    canonical_url = "https://cdn.hakwonplus.com"
    assert canonical_url in base_settings
    assert "pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev" not in base_settings
    assert "CDN_HLS_SIGNING_SECRET must be set via SSM/env" in prod_settings
    assert "CDN_HLS_SIGNING_KEY_ID must be" in prod_settings
    assert "Assert-ApiVideoPlaybackEnv" in sync_env
    assert (
        "Assert-ApiVideoPlaybackEnv -EnvObject $obj "
        "-ParameterName $script:SsmApiEnv"
    ) in sync_env
    assert "Refusing to deploy broken video playback" in sync_env
    assert "Refusing to deploy unsigned video playback" in sync_env
    assert "Refusing to deploy an unknown CDN signing key" in sync_env
    assert f"CDN_HLS_BASE_URL={canonical_url}" in env_example


def test_video_cdn_deploy_guard_behavior() -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell is not available")

    sync_env_path = str(SYNC_ENV).replace("'", "''")
    script = rf"""
. '{sync_env_path}'
$good = [PSCustomObject]@{{
    CDN_HLS_BASE_URL = 'https://cdn.hakwonplus.com/'
    CDN_HLS_SIGNING_SECRET = ('x' * 32)
    CDN_HLS_SIGNING_KEY_ID = 'v1'
}}
Assert-ApiVideoPlaybackEnv -EnvObject $good -ParameterName '/academy/api/env'

$invalid = @(
    [PSCustomObject]@{{
        CDN_HLS_BASE_URL = 'https://example.r2.dev'
        CDN_HLS_SIGNING_SECRET = ('x' * 32)
        CDN_HLS_SIGNING_KEY_ID = 'v1'
    }},
    [PSCustomObject]@{{
        CDN_HLS_BASE_URL = 'https://cdn.hakwonplus.com'
        CDN_HLS_SIGNING_SECRET = ''
        CDN_HLS_SIGNING_KEY_ID = 'v1'
    }},
    [PSCustomObject]@{{
        CDN_HLS_BASE_URL = 'https://cdn.hakwonplus.com'
        CDN_HLS_SIGNING_SECRET = ('x' * 32)
        CDN_HLS_SIGNING_KEY_ID = ''
    }}
)
foreach ($candidate in $invalid) {{
    $blocked = $false
    try {{
        Assert-ApiVideoPlaybackEnv -EnvObject $candidate -ParameterName '/academy/api/env'
    }} catch {{
        $blocked = $true
    }}
    if (-not $blocked) {{ exit 41 }}
}}
exit 0
"""
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_cdn_signing_secret_stays_masked_in_verbose_output() -> None:
    assert _mask("super-secret-value", "CDN_HLS_SIGNING_SECRET", True) == "su***"
