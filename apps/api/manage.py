#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    # 🔴 academy 루트를 PYTHONPATH에 추가
    BASE_DIR = Path(__file__).resolve().parents[2]
    sys.path.append(str(BASE_DIR))

    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "apps.api.config.settings.dev"
    )

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
