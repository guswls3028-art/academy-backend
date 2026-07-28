from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "base.py"
WORKER_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "worker.py"
PROD_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "prod.py"
SYNC_ENV = REPO_ROOT / "scripts" / "v1" / "core" / "sync_env.ps1"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_video_cdn_runtime_is_fail_closed() -> None:
    base_settings = BASE_SETTINGS.read_text(encoding="utf-8")
    worker_settings = WORKER_SETTINGS.read_text(encoding="utf-8")
    prod_settings = PROD_SETTINGS.read_text(encoding="utf-8")
    sync_env = SYNC_ENV.read_text(encoding="utf-8-sig")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    canonical_url = "https://cdn.hakwonplus.com"
    assert canonical_url in base_settings
    assert canonical_url in worker_settings
    assert "pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev" not in base_settings
    assert "pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev" not in worker_settings
    assert "CDN_HLS_SIGNING_SECRET must be set via SSM/env" in prod_settings
    assert "Assert-ApiVideoPlaybackEnv" in sync_env
    assert "Refusing to deploy broken video playback" in sync_env
    assert "Refusing to deploy unsigned video playback" in sync_env
    assert f"CDN_HLS_BASE_URL={canonical_url}" in env_example
