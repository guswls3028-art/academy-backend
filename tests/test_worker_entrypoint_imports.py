from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


WORKER_ENTRYPOINT_MODULES = (
    "apps.worker.ai_worker.sqs_main_cpu",
    "apps.worker.ai_worker.sqs_main_gpu",
    "apps.worker.tools_worker.sqs_main",
    "apps.worker.messaging_worker.sqs_main",
    "apps.worker.video_worker.batch_entrypoint",
    "apps.worker.video_worker.batch_main",
)


def test_worker_entrypoints_import_under_worker_settings():
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
    env["SECRET_KEY"] = "test-worker-entrypoints"
    env["PYTHONPATH"] = str(backend_root) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("DB_NAME", "test")
    env.setdefault("DB_USER", "test")
    env.setdefault("DB_PASSWORD", "test")
    env.setdefault("DB_HOST", "localhost")
    env.setdefault("DB_PORT", "5432")

    script = "\n".join(
        [
            "import importlib",
            f"modules = {WORKER_ENTRYPOINT_MODULES!r}",
            "for module in modules:",
            "    importlib.import_module(module)",
            "print('OK worker entrypoint imports')",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(backend_root),
        timeout=30,
    )

    assert result.returncode == 0, (
        "Worker entrypoint import smoke failed under worker settings.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
