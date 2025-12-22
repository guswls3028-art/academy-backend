#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def main():
    """Run administrative tasks."""

    # 🔥 프로젝트 루트 (academy/)
    BASE_DIR = Path(__file__).resolve().parent

    # 🔥 핵심: PYTHONPATH에 'apps'를 직접 올리지 않는다
    # academy/ 만 올리고, apps는 네임스페이스로만 사용
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "apps.api.config.settings.dev",
    )

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
