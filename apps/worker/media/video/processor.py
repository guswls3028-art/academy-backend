# apps/worker/media/video/processor.py

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

import requests

from .transcoder import transcode_to_hls


# ---------------------------------------------------------------------
# Errors (processor -> task boundary)
# ---------------------------------------------------------------------

class MediaProcessingError(RuntimeError):
    """
    Processor-level error with structured context.
    Task layer should catch this and mark Video as FAILED.
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
      - download source to local
      - duration (ffprobe local)
      - thumbnail (ffmpeg local)
      - HLS (transcoder.transcode_to_hls local)
      - verification (master.m3u8 exists)
    """

    def run(
        self,
        *,
        video_id: int,
        input_url: str,
        output_root: Union[str, Path],
        timeout_download: Optional[int] = 60 * 30,
        timeout_probe: Optional[int] = 60,
        timeout_thumbnail: Optional[int] = 120,
        timeout_hls: Optional[int] = None,
        cleanup_source: bool = False,
    ) -> ProcessResult:
        out_root = Path(output_root)

        # 1) pre-clean
        self._pre_clean_output_root(
            video_id=video_id,
            output_root=out_root,
        )

        # 2) download source to local (정석)
        local_input_path = self._download_source(
            video_id=video_id,
            input_url=input_url,
            output_root=out_root,
            timeout=timeout_download,
        )

        try:
            # 3) probe duration (local)
            duration = self._probe_duration_seconds(
                video_id=video_id,
                input_path=str(local_input_path),
                timeout=timeout_probe,
            )

            # 4) thumbnail (local)
            thumb_path = out_root / "thumbnail.jpg"
            self._generate_thumbnail(
                video_id=video_id,
                input_path=str(local_input_path),
                output_path=thumb_path,
                timeout=timeout_thumbnail,
            )

            # 5) HLS transcode (local)
            master_path = self._transcode_hls(
                video_id=video_id,
                input_path=str(local_input_path),
                timeout=timeout_hls,
            )

            # 6) verify
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

        finally:
            if cleanup_source:
                try:
                    if local_input_path.exists():
                        local_input_path.unlink()
                except Exception:
                    pass

    # -----------------------------------------------------------------
    # Internal steps
    # -----------------------------------------------------------------

    def _pre_clean_output_root(self, *, video_id: int, output_root: Path) -> None:
        """
        Delete output_root entirely if exists, then recreate.
        Prevents stale artifacts from making retries look successful.
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

    def _download_source(
        self,
        *,
        video_id: int,
        input_url: str,
        output_root: Path,
        timeout: Optional[int] = 60 * 30,  # 30m
        chunk_size: int = 1024 * 1024,      # 1MB
    ) -> Path:
        """
        Download source MP4 from presigned GET URL to local disk.
        """
        output_root.mkdir(parents=True, exist_ok=True)

        final_path = output_root / "_source.mp4"
        tmp_path = output_root / "_source.mp4.part"

        try:
            with requests.get(input_url, stream=True, timeout=30) as r:
                r.raise_for_status()

                expected_len = r.headers.get("Content-Length")
                expected_len_int = int(expected_len) if expected_len and expected_len.isdigit() else None

                written = 0
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)

                if expected_len_int is not None and written != expected_len_int:
                    raise RuntimeError(
                        f"download size mismatch (expected={expected_len_int}, got={written})"
                    )

            tmp_path.replace(final_path)
            return final_path

        except Exception as e:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

            raise MediaProcessingError(
                stage="DOWNLOAD",
                code="DOWNLOAD_FAILED",
                message=f"Failed to download source (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "input_url": input_url[:2000],
                    "output_root": str(output_root),
                    "error": repr(e),
                },
            ) from e

    def _probe_duration_seconds(
        self,
        *,
        video_id: int,
        input_path: str,
        timeout: Optional[int],
    ) -> float:
        """
        ffprobe local file.
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            input_path,
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
                    "stdout": p.stdout[:4000],
                    "error": repr(e),
                },
            ) from e

        if duration <= 0:
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
        input_path: str,
        output_path: Path,
        timeout: Optional[int],
    ) -> None:
        """
        Generate one thumbnail jpg from local file.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
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
        input_path: str,
        timeout: Optional[int],
    ) -> Path:
        """
        Delegate to transcoder (local input).
        """
        try:
            master_path = transcode_to_hls(
                video_id=video_id,
                input_path=input_path,
                timeout=timeout,
            )
            return master_path
        except Exception as e:
            raise MediaProcessingError(
                stage="HLS",
                code="HLS_FAILED",
                message=f"HLS transcode failed (video_id={video_id})",
                context={
                    "video_id": video_id,
                    "input_path": input_path,
                    "error": repr(e),
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
# Public entry
# ---------------------------------------------------------------------

def run(
    *,
    video_id: int,
    input_url: str,
    output_root: Union[str, Path],
    timeout_download: Optional[int] = 60 * 30,
    timeout_probe: Optional[int] = 60,
    timeout_thumbnail: Optional[int] = 120,
    timeout_hls: Optional[int] = None,
) -> ProcessResult:
    return VideoProcessor().run(
        video_id=video_id,
        input_url=input_url,
        output_root=output_root,
        timeout_download=timeout_download,
        timeout_probe=timeout_probe,
        timeout_thumbnail=timeout_thumbnail,
        timeout_hls=timeout_hls,
    )
