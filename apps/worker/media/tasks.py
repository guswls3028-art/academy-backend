# worker/media/tasks.py
# Worker 프로세스가 media task를 확실히 import하도록 고정점 역할
from apps.shared.tasks.media import process_video_media  # noqa: F401
# worker 쪽 코드 예시 (apps/shared/tasks/media.py 내부)

import requests
from django.conf import settings

def notify_processing_complete(*, video_id: int, hls_path: str, duration: int | None):
    url = f"{settings.API_BASE_URL}/internal/videos/{video_id}/processing-complete/"
    requests.post(
        url,
        json={
            "hls_path": hls_path,
            "duration": duration,
        },
        timeout=5,
    )
