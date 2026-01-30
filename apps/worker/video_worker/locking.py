from __future__ import annotations

import os
import time
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class LockHandle:
    path: Path
    fd: int


class LockBusyError(RuntimeError):
    pass


def acquire_video_lock(lock_dir: str, video_id: int, stale_seconds: int) -> LockHandle:
    """
    로컬 idempotency lock:
    - single host에서 중복처리 방지
    - stale lock은 mtime 기반으로 회수

    NOTE:
    - multi-host는 backend lease까지 있어야 완벽 (요구사항상 최소 하나 구현이면 OK)
    """
    Path(lock_dir).mkdir(parents=True, exist_ok=True)
    path = Path(lock_dir) / f"video_{video_id}.lock"

    now = int(time.time())
    pid = os.getpid()
    payload = f"pid={pid}\ncreated_at={now}\n"

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, payload.encode())
        os.fsync(fd)
        return LockHandle(path=path, fd=fd)

    except FileExistsError:
        try:
            stat = path.stat()
            age = now - int(stat.st_mtime)
            if age > int(stale_seconds):
                try:
                    path.unlink()
                except Exception:
                    pass
                return acquire_video_lock(lock_dir, video_id, stale_seconds)
        except Exception:
            # stat 실패 시에도 lock은 존중
            pass

        raise LockBusyError(f"video {video_id} already processing")


def release_video_lock(handle: LockHandle) -> None:
    try:
        os.close(handle.fd)
    except Exception:
        pass
    try:
        handle.path.unlink()
    except Exception:
        pass
