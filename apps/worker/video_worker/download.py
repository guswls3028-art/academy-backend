from __future__ import annotations

import logging
from pathlib import Path

import requests

from apps.worker.video_worker.config import Config
from apps.worker.video_worker.utils import backoff_sleep, ensure_dir, trim_tail

logger = logging.getLogger("video_worker")


class DownloadError(RuntimeError):
    pass


def download_to_file(*, url: str, dst: Path, cfg: Config) -> None:
    """
    안정적 다운로드:
    - stream chunk
    - retry with backoff
    - tmp(.part) -> atomic rename
    """
    ensure_dir(dst.parent)

    attempt = 0
    while True:
        try:
            with requests.get(url, stream=True, timeout=cfg.DOWNLOAD_TIMEOUT_SECONDS) as r:
                r.raise_for_status()

                tmp = dst.with_suffix(dst.suffix + ".part")
                bytes_written = 0

                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=cfg.DOWNLOAD_CHUNK_BYTES):
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)

                if bytes_written <= 0:
                    raise DownloadError("downloaded file is empty")

                tmp.replace(dst)
                return

        except Exception as e:
            attempt += 1
            if attempt >= cfg.RETRY_MAX_ATTEMPTS:
                raise DownloadError(f"download failed: {trim_tail(str(e))}") from e
            logger.warning("download retry attempt=%s err=%s", attempt, e)
            backoff_sleep(attempt, cfg.BACKOFF_BASE_SECONDS, cfg.BACKOFF_CAP_SECONDS)
