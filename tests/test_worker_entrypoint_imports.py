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

AI_WORKER_RUNTIME_MODULES = (
    "apps.domains.results.services.grading_service",
    "apps.domains.submissions.services.ai_omr_result_mapper",
    "apps.domains.tools.problem_studio.conversion_worker",
    "apps.domains.tools.problem_studio.conversion_ai",
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
            "import importlib.abc",
            "import sys",
            f"modules = {WORKER_ENTRYPOINT_MODULES!r}",
            "for module in modules:",
            "    importlib.import_module(module)",
            "class BlockRestFramework(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname == 'rest_framework' or fullname.startswith('rest_framework.'):",
            "            raise ModuleNotFoundError(f\"No module named '{fullname}'\")",
            "        return None",
            "sys.meta_path.insert(0, BlockRestFramework())",
            f"runtime_modules = {AI_WORKER_RUNTIME_MODULES!r}",
            "for module in runtime_modules:",
            "    importlib.import_module(module)",
            "from apps.worker.messaging_worker.sqs_main import _assert_grade_homework_delivery_active",
            "for event in ('manual_send', 'assignment_not_submitted'):",
            "    message = {'event_type': event}",
            "    _assert_grade_homework_delivery_active(message, business_tenant_id=1, business_key='test')",
            "    message['domain_object_id'] = 'grade_homework_outbox:invalid'",
            "    try:",
            "        _assert_grade_homework_delivery_active(message, business_tenant_id=1, business_key='test')",
            "    except ValueError as error:",
            "        assert str(error) == 'grade_homework_review_changed'",
            "    else:",
            "        raise AssertionError('invalid homework reference passed the worker guard')",
            "from apps.support.messaging.student_dependencies import assert_current_delivery_contact, students_for_tenant",
            "assert_current_delivery_contact({'target_type': 'observer'}, business_tenant_id=1)",
            "assert_current_delivery_contact({'target_type': 'account'}, business_tenant_id=1)",
            "try:",
            "    assert_current_delivery_contact({'target_type': 'account', 'domain_object_id': 'enrollment-notice:invalid'}, business_tenant_id=1)",
            "except ValueError as error:",
            "    assert str(error) == 'recipient_contact_changed'",
            "else:",
            "    raise AssertionError('invalid enrollment receipt passed the worker guard')",
            "assert not list(students_for_tenant(1).filter(pk__in=[]))",
            "from apps.domains.homework_results.models import Homework",
            "from apps.domains.lectures.models import Session",
            "from apps.support.messaging.assignment_not_submitted_dependencies import missing_homework_assignments",
            "homework = Homework(pk=1, session=Session(pk=1, lecture_id=1))",
            "assert missing_homework_assignments(tenant=1, homework=homework, student_ids=[]) == []",
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
