"""Tools Worker entrypoint.

Handles non-AI document conversion jobs such as PPT generation on a lightweight
queue, separate from the heavier AI/OCR worker image and autoscaling policy.
"""
from __future__ import annotations

import logging
import os
import sys

if os.environ.get("DJANGO_SETTINGS_MODULE"):
    import django
    django.setup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [TOOLS-SQS-WORKER] %(message)s",
    )
    from academy.adapters.queue.sqs.tools_queue import SQSToolsQueueAdapter
    from academy.framework.workers.ai_sqs_worker import run_ai_sqs_worker
    from apps.domains.ai.queueing.worker_job_types import TOOL_WORKER_JOB_TYPES

    sys.exit(
        run_ai_sqs_worker(
            queue=SQSToolsQueueAdapter(),
            worker_kind="tools",
            supported_job_types=TOOL_WORKER_JOB_TYPES,
        )
    )
