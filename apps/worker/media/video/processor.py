# apps/worker/media/video/processor.py

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .transcoder import transcode_to_hls


# ---------------------------------------------------------------------
# Errors (processor -> task boundary)
# ---------------------------------------------------------------------

class MediaProcessingError(RuntimeError):
    """
    Processor-level error with structured context.
    Task layer should catch this and mark Video as FAILED (no retry decision here).
    """

    def __init__(
        self,
        *,
        stage: str,
        code: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.context = context or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": str(self),
            "context": self.context,
        }


# ---------------------------------------------------------------------
# Result contract (processor -> task)
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessResult:
    video_id: int
    duration_seconds: float
    thumbnail_path: Path
    master_playlist_path: Path
    output_root: Path


# ---------------------------------------------------------------------
# Processor (single responsibility: make one video READY)
# ---------------------------------------------------------------------

class VideoProcessor:
    """
    The single authority that turns one uploaded video into READY artifacts:
      - duration (ffprobe)
      - thumbnail (ffmpeg)
      - HLS (transcoder.transcode_to_hls)
      - verification (master.m3u8 exists)
    """

    def run(
        self,
        *,
        video_id: int,
        input_url: str,
        output_root: Union[str, Path],
        timeout_probe: Optional[int] = 60,
        timeout_thumbnail: Optional[int] = 120,
        timeout_hls: Optional[int] = None,
    ) -> ProcessResult:
        out_root = Path(output_root)

        # 1) pre-clean
        self._pre_clean_output_root(
            video_id=video_id,
            output_root=out_root,
        )

        # 2) probe duration
        duration = self._probe_duration_seconds(
            video_id=video_id,
            input_url=input_url,
            timeout=timeout_probe,
        )

        # 3) thumbnail
        thumb_path = out_root / "thumbnail.jpg"
        self._generate_thumbnail(
            video_id=video_id,
            input_url=input_url,
            output_path=thumb_path,
            timeout=timeout_thumbnail,
        )

        # 4) HLS transcode (delegated to fixed transcoder)
        master_path = self._transcode_hls(
            video_id=video_id,
            input_url=input_url,
            output_root=out_root,
            timeout=timeout_hls,
        )

        # 5) verify (READY condition: master.m3u8 exists)
        self._verify_ready(
            video_id=video_id,
            master_path=master_path,
        )

        return ProcessResult(
            video_id=video_id,
            duration_seconds=duration,
            thumbnail_path=thumb_path,
            master_playlist_path=master_path,
            output_root=out_root,
        )

    # -----------------------------------------------------------------
    # Internal steps
    # -----------------------------------------------------------------

    def _pre_clean_output_root(self, *, video_id: int, output_root: Path) -> None:
        """
        Delete output_root entirely if exists, then recreate.
        This prevents 'stale artifacts' from making retries look successful.
        """
        try:
            if output_root.exists():
                shutil.rmtree(output_root)
            output_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise MediaProcessingError(
                stage="CLEAN",
                code="CLEAN_FAILED",
                message=f"Failed to pre-clean output_root (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "output_root": str(output_root),
                    "error": repr(e),
                },
            ) from e

    def _probe_duration_seconds(
        self,
        *,
        video_id: int,
        input_url: str,
        timeout: Optional[int],
    ) -> float:
        """
        ffprobe via streaming URL. No local download.
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            input_url,
        ]

        try:
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception as e:
            raise MediaProcessingError(
                stage="PROBE",
                code="PROBE_FAILED",
                message=f"ffprobe execution failed (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "error": repr(e),
                },
            ) from e

        if p.returncode != 0:
            raise MediaProcessingError(
                stage="PROBE",
                code="PROBE_FAILED",
                message=f"ffprobe returned non-zero (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "returncode": p.returncode,
                    "stderr": p.stderr,
                },
            )

        try:
            data = json.loads(p.stdout)
            fmt = data.get("format") or {}
            duration_str = fmt.get("duration")
            duration = float(duration_str) if duration_str is not None else 0.0
        except Exception as e:
            raise MediaProcessingError(
                stage="PROBE",
                code="PROBE_FAILED",
                message=f"Failed to parse ffprobe output (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "stdout": p.stdout[:4000],  # keep bounded
                    "error": repr(e),
                },
            ) from e

        if duration <= 0:
            # duration 0이면 이후 UX/정산 등에서 문제되므로 여기서 실패로 올림
            raise MediaProcessingError(
                stage="PROBE",
                code="PROBE_FAILED",
                message=f"Invalid duration from ffprobe (video_id={video_id}, duration={duration})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "duration": duration,
                },
            )

        return duration

    def _generate_thumbnail(
        self,
        *,
        video_id: int,
        input_url: str,
        output_path: Path,
        timeout: Optional[int],
    ) -> None:
        """
        Generate one thumbnail jpg. Streaming input. No local download.

        Note:
        - Using a safe seek pattern: try -ss before -i may be unreliable for some HTTP sources,
          so we keep it simple: capture a frame near the start.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_url,
            "-vf", "thumbnail,scale=1280:-2",
            "-frames:v", "1",
            str(output_path),
        ]

        try:
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception as e:
            raise MediaProcessingError(
                stage="THUMBNAIL",
                code="THUMBNAIL_FAILED",
                message=f"ffmpeg thumbnail execution failed (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "error": repr(e),
                },
            ) from e

        if p.returncode != 0:
            raise MediaProcessingError(
                stage="THUMBNAIL",
                code="THUMBNAIL_FAILED",
                message=f"ffmpeg thumbnail returned non-zero (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "cmd": cmd,
                    "returncode": p.returncode,
                    "stderr": p.stderr,
                },
            )

        if not output_path.exists():
            raise MediaProcessingError(
                stage="THUMBNAIL",
                code="THUMBNAIL_FAILED",
                message=f"thumbnail file not created (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "output_path": str(output_path),
                },
            )

    def _transcode_hls(
        self,
        *,
        video_id: int,
        input_url: str,
        output_root: Path,
        timeout: Optional[int],
    ) -> Path:
        """
        Delegate to fixed transcoder. Any error should be wrapped with stage/code.
        """
        try:
            master_path = transcode_to_hls(
                video_id=video_id,
                input_url=input_url,
                output_root=output_root,
                timeout=timeout,
            )
            return master_path
        except Exception as e:
            # transcoder already raises rich RuntimeError(dict) in many cases.
            # We wrap it to unify error boundary for task.
            raise MediaProcessingError(
                stage="HLS",
                code="HLS_FAILED",
                message=f"HLS transcode failed (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "output_root": str(output_root),
                    "error": repr(e),
                    # if underlying exception contains dict-like args, task can still log repr(e)
                },
            ) from e

    def _verify_ready(self, *, video_id: int, master_path: Path) -> None:
        """
        READY criterion is strictly: master.m3u8 exists.
        """
        if not master_path.exists():
            raise MediaProcessingError(
                stage="VERIFY",
                code="OUTPUT_INVALID",
                message=f"READY criterion failed: master.m3u8 missing (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "master_path": str(master_path),
                },
            )


# ---------------------------------------------------------------------
# Public entry (simple functional style)
# ---------------------------------------------------------------------

def run(
    *,
    video_id: int,
    input_url: str,
    output_root: Union[str, Path],
    timeout_probe: Optional[int] = 60,
    timeout_thumbnail: Optional[int] = 120,
    timeout_hls: Optional[int] = None,
) -> ProcessResult:
    """
    Public entry point used by task layer.
    Keeps dependency surface minimal: processor.run(...) only.
    """
    return VideoProcessor().run(
        video_id=video_id,
        input_url=input_url,
        output_root=output_root,
        timeout_probe=timeout_probe,
        timeout_thumbnail=timeout_thumbnail,
        timeout_hls=timeout_hls,
    )
