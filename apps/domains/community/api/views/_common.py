"""Community views — 공통 상수/헬퍼 (첨부 화이트리스트, 파일명 sanitize, tenant resolve)."""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_ATTACHMENTS_PER_POST = 10

# 화이트리스트 — 운영팀이 추가 요청 시 명시적으로 확장
ALLOWED_CONTENT_TYPE_PREFIXES = (
    "image/",
    "video/",
    "audio/",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.hancom.hwp",
    "application/x-hwp",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/gzip",
    "text/plain",
    "text/csv",
)

# 확장자 차단 — 브라우저 콘텐츠 스니핑/실행 위험
BLOCKED_EXTENSIONS = frozenset({
    "html", "htm", "xhtml", "xht", "shtml",
    "svg", "svgz",  # SVG 안에 <script> 가능
    "exe", "com", "bat", "cmd", "msi", "scr",
    "sh", "bash", "ps1", "vbs", "js", "jse", "wsf", "wsh",
    "jar", "apk", "ipa",
    "lnk",
})

# SVG는 image/svg+xml MIME으로도 위장 가능 → MIME 단계에서도 차단
BLOCKED_CONTENT_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/javascript",
    "text/javascript",
})


def _get_tenant_from_request(request):
    """request.tenant 반환. 테넌트 미해석 시 None (폴백 없음 — §B 절대 격리)."""
    return getattr(request, "tenant", None)


def sanitize_filename(name: str, max_len: int = 200) -> str:
    """파일명 안전화 — 경로 분리자/제어문자/매우 긴 유니코드 차단."""
    if not name:
        return "file"
    name = unicodedata.normalize("NFC", name)
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.strip(" .")
    if not name:
        return "file"
    if len(name) > max_len:
        # 확장자 보존
        if "." in name:
            base, _, ext = name.rpartition(".")
            name = base[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def get_extension(filename: str) -> str:
    """확장자 (소문자, 점 제외). 없으면 빈 문자열."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def is_attachment_allowed(filename: str, content_type: str) -> tuple[bool, str]:
    """첨부파일 허용 여부. (allowed, reason)."""
    ext = get_extension(filename)
    if ext in BLOCKED_EXTENSIONS:
        return False, f"보안상 허용되지 않는 확장자입니다: .{ext}"
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct in BLOCKED_CONTENT_TYPES:
        return False, f"보안상 허용되지 않는 파일 형식입니다: {ct}"
    if not ct:
        return True, ""  # 비어 있으면 octet-stream 폴백 허용
    if not any(ct.startswith(p) for p in ALLOWED_CONTENT_TYPE_PREFIXES):
        return False, f"허용되지 않는 파일 형식입니다: {ct}"
    return True, ""
