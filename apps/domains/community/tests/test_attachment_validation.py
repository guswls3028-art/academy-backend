"""첨부파일 화이트리스트/파일명 sanitize 단위 테스트."""
from apps.domains.community.api.views._common import (
    is_attachment_allowed,
    sanitize_filename,
    get_extension,
)


# ── is_attachment_allowed ──────────────────────────────────

def test_allowed_image():
    ok, _ = is_attachment_allowed("photo.jpg", "image/jpeg")
    assert ok


def test_allowed_pdf():
    ok, _ = is_attachment_allowed("doc.pdf", "application/pdf")
    assert ok


def test_allowed_office():
    ok, _ = is_attachment_allowed("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert ok


def test_allowed_hwp():
    ok, _ = is_attachment_allowed("doc.hwp", "application/x-hwp")
    assert ok


def test_blocked_html_extension():
    ok, reason = is_attachment_allowed("evil.html", "text/plain")
    assert not ok
    assert "html" in reason


def test_blocked_svg_extension():
    """SVG 안에 <script> 가능 → 차단."""
    ok, _ = is_attachment_allowed("logo.svg", "image/svg+xml")
    assert not ok


def test_blocked_svg_via_image_mime():
    """이미지 MIME이지만 svg+xml은 차단."""
    ok, _ = is_attachment_allowed("file.png", "image/svg+xml")
    assert not ok


def test_blocked_executable_extension():
    for ext in ("exe", "bat", "sh", "ps1", "vbs", "js"):
        ok, _ = is_attachment_allowed(f"x.{ext}", "application/octet-stream")
        assert not ok, f".{ext} should be blocked"


def test_blocked_html_mime_even_with_safe_ext():
    ok, _ = is_attachment_allowed("file.txt", "text/html")
    assert not ok


def test_unknown_mime_rejected():
    ok, _ = is_attachment_allowed("file.weird", "application/x-evil")
    assert not ok


def test_empty_mime_allowed():
    """브라우저가 MIME을 안 보내는 경우 octet-stream 폴백 허용."""
    ok, _ = is_attachment_allowed("file.txt", "")
    assert ok


# ── sanitize_filename ──────────────────────────────────────

def test_sanitize_strips_path_traversal():
    assert "/" not in sanitize_filename("../../etc/passwd")
    assert "\\" not in sanitize_filename("..\\..\\windows\\system")


def test_sanitize_strips_control_chars():
    assert "\x00" not in sanitize_filename("evil\x00.txt")


def test_sanitize_preserves_korean():
    out = sanitize_filename("문서.pdf")
    assert "문서" in out
    assert out.endswith(".pdf")


def test_sanitize_truncates_long_name():
    long_name = "a" * 500 + ".pdf"
    out = sanitize_filename(long_name, max_len=200)
    assert len(out) <= 200
    assert out.endswith(".pdf")


def test_sanitize_empty_returns_default():
    assert sanitize_filename("") == "file"
    assert sanitize_filename("   ") == "file"


def test_get_extension():
    assert get_extension("a.PDF") == "pdf"
    assert get_extension("noext") == ""
    assert get_extension("a.b.c.txt") == "txt"
