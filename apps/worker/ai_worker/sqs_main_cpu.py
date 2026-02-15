"""
AI Worker CPU — Academy 전용 엔트리 (Lite + Basic)

Legacy 제거: academy 전용 엔트리.
academy.framework.workers.ai_sqs_worker 만 사용.
"""
from __future__ import annotations

import os
import sys

if os.environ.get("DJANGO_SETTINGS_MODULE"):
    import django
    django.setup()

if __name__ == "__main__":
    from academy.framework.workers.ai_sqs_worker import run_ai_sqs_worker
    sys.exit(run_ai_sqs_worker())
