#!/usr/bin/env python
import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def main():
    BASE_DIR = Path(__file__).resolve().parents[2]
    sys.path.append(str(BASE_DIR))

    # ✅ .env 로드
    load_dotenv(BASE_DIR / ".env")

    # ✅ 운영 기준: base 단일
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "apps.api.config.settings.base"
    )

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
