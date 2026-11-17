"""Offline build smoke for the packaged lecture HLS transcoder."""

import json
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory

from academy.adapters.video.transcoder import FFmpegProcessError, transcode_to_hls


def run(*args: str) -> bytes:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, timeout=45).stdout


def probe(path: Path) -> list[dict]:
    return json.loads(run(
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_name,width,height:stream_disposition=default,attached_pic", "-of", "json", str(path),
    ))["streams"]


def exercise(root: Path, cover: Path, size: str, expected: dict[str, tuple[int, int]]) -> None:
    source = root / f"{size}.mp4"
    run(
        "ffmpeg", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=12",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
        "-disposition:a:0", "0", "-disposition:a:1", "default",
        "-t", "2", "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast",
        "-c:a", "aac", str(source),
    )
    source_streams = probe(source)
    assert [stream["codec_name"] for stream in source_streams] == ["h264", "aac", "aac"], source_streams
    assert [stream["disposition"]["default"] for stream in source_streams[1:]] == [0, 1], source_streams
    with_cover = root / f"{size}-with-cover.mp4"
    run(
        "ffmpeg", "-v", "error", "-i", str(source), "-i", str(cover),
        "-map", "1:v:0", "-map", "0", "-c", "copy",
        "-disposition:v:0", "attached_pic", str(with_cover),
    )
    source_streams = probe(with_cover)
    assert any(
        stream["codec_name"] == "mjpeg" and stream["disposition"]["attached_pic"]
        for stream in source_streams
    ), source_streams
    master = transcode_to_hls(
        video_id=1, input_path=str(with_cover), output_root=root / size,
        ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", hls_time=4, timeout=45, duration_sec=2,
    )
    variants = [line for line in master.read_text().splitlines() if line and not line.startswith("#")]
    assert variants == list(expected), variants
    for variant in variants:
        playlist = master.parent / variant
        streams = probe(playlist)
        assert [stream["codec_name"] for stream in streams] == ["h264", "aac"], streams
        width, height = expected[variant]
        assert (streams[0]["width"], streams[0]["height"]) == (width, height), streams
        frames = run("ffmpeg", "-v", "error", "-i", str(playlist), "-map", "0:v:0", "-f", "framemd5", "-")
        checksums = {line.rsplit(b",", 1)[-1].strip() for line in frames.splitlines()
                     if line and not line.startswith(b"#")}
        assert len(checksums) > 1, len(checksums)
        pcm = run(
            "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(playlist), "-t", "1",
            "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
        )
        assert len(pcm) == 32000, len(pcm)
        samples = [sample for (sample,) in struct.iter_unpack("<h", pcm)]
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        assert 655 <= crossings <= 665, crossings
        print(f"Lecture HLS {size} {variant}: {width}x{height}, AAC, {crossings} Hz", flush=True)


def exercise_cover_only(root: Path, cover: Path) -> None:
    source = root / "audio-with-cover.mp4"
    output = root / "audio-with-cover"
    run(
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-i", str(cover),
        "-map", "0:a:0", "-map", "1:v:0", "-c:a", "aac", "-c:v", "copy",
        "-disposition:v:0", "attached_pic", str(source),
    )
    assert any(stream["disposition"]["attached_pic"] for stream in probe(source))
    try:
        transcode_to_hls(
            video_id=1, input_path=str(source), output_root=output,
            ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", hls_time=4, timeout=45, duration_sec=1,
        )
    except FFmpegProcessError as error:
        assert error.invalid_source, error.diagnostics
    else:
        raise AssertionError("Attached artwork was accepted as playable video")
    assert not (output / "master.m3u8").exists()
    print("Lecture HLS audio with attached artwork: permanent no-video failure", flush=True)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="lecture-transcoder-smoke-") as directory:
        root = Path(directory)
        cover = root / "cover.jpg"
        run(
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x32",
            "-frames:v", "1", "-c:v", "mjpeg", "-threads", "1", str(cover),
        )
        exercise(root, cover, "640x360", {"v1/index.m3u8": (640, 360)})
        exercise(root, cover, "1600x900", {"v2/index.m3u8": (1600, 900), "v1/index.m3u8": (1280, 720)})
        exercise_cover_only(root, cover)
