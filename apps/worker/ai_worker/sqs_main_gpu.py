"""
AI Worker GPU — Academy 전용 엔트리 (Premium)

Legacy 제거: legacy main() 제거.
academy.framework.workers.ai_sqs_worker 만 사용 (AI_WORKER_PREMIUM_ONLY=1).
"""
from __future__ import annotations

import os
import sys

if os.environ.get("DJANGO_SETTINGS_MODULE"):
    import django
    django.setup()

if __name__ == "__main__":
    os.environ["AI_WORKER_PREMIUM_ONLY"] = "1"
    from academy.framework.workers.ai_sqs_worker import run_ai_sqs_worker
    sys.exit(run_ai_sqs_worker())
