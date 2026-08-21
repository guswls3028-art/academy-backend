from __future__ import annotations

from pathlib import PurePath


MAX_SOURCE_FILE_SIZE = 50 * 1024 * 1024

AUTO_SEGMENT_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".hwp", ".hwpx"})
AUTO_PAIR_PRIMARY_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
AUTO_PAIR_SUPPORT_SUFFIXES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".hwp", ".hwpx"}
)

# Source documents are intentionally broad. Only formats that can execute code
# or render active browser content are rejected at the upload boundary.
BLOCKED_SOURCE_SUFFIXES = frozenset(
    {
        ".apk",
        ".bash",
        ".bat",
        ".cjs",
        ".chm",
        ".cmd",
        ".com",
        ".dll",
        ".dmg",
        ".docm",
        ".exe",
        ".gadget",
        ".hta",
        ".htm",
        ".html",
        ".ipa",
        ".iso",
        ".jar",
        ".js",
        ".jse",
        ".lnk",
        ".mjs",
        ".msi",
        ".php",
        ".pptm",
        ".ps1",
        ".py",
        ".rb",
        ".reg",
        ".scr",
        ".sh",
        ".shtml",
        ".svg",
        ".svgz",
        ".sys",
        ".vbs",
        ".wsf",
        ".wsh",
        ".xhtml",
        ".xlsm",
    }
)
BLOCKED_SOURCE_CONTENT_TYPES = frozenset(
    {
        "application/javascript",
        "application/x-msdownload",
        "application/x-sh",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/html",
        "text/javascript",
    }
)


def safe_source_filename(raw_name: object) -> str:
    name = str(raw_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character >= " " and character != "\x7f")
    name = name.strip().strip(".")
    if not name:
        return "source-file"
    if len(name) <= 220:
        return name
    suffix = PurePath(name).suffix
    if not suffix or len(suffix) >= 40:
        return name[:220]
    return f"{name[: 220 - len(suffix)]}{suffix}"


def source_suffix(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def validate_source_upload(upload: object) -> tuple[str, str, str | None]:
    filename = safe_source_filename(getattr(upload, "name", ""))
    suffix = source_suffix(filename)
    size = int(getattr(upload, "size", 0) or 0)
    content_type = str(getattr(upload, "content_type", "") or "")
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()

    if size <= 0:
        return filename, suffix, "빈 파일은 업로드할 수 없습니다."
    if size > MAX_SOURCE_FILE_SIZE:
        return filename, suffix, "파일 크기는 50MB 이하여야 합니다."
    if suffix in BLOCKED_SOURCE_SUFFIXES or normalized_content_type in BLOCKED_SOURCE_CONTENT_TYPES:
        return (
            filename,
            suffix,
            "실행 파일, 스크립트 또는 브라우저 실행 형식은 업로드할 수 없습니다.",
        )
    return filename, suffix, None


def storage_content_type(upload: object, suffix: str) -> str:
    if suffix in AUTO_SEGMENT_SUFFIXES:
        return str(getattr(upload, "content_type", "") or "application/octet-stream")
    return "application/octet-stream"
