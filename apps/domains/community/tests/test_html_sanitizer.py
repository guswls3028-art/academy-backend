"""HTML sanitizer — XSS/CSS injection/tabnabbing 방어 단위 테스트."""
from apps.domains.community.services.html_sanitizer import sanitize_html


def test_strips_script_tags():
    assert "<script>" not in sanitize_html("<p>x</p><script>alert(1)</script>")


def test_strips_event_handlers():
    out = sanitize_html('<a href="x" onclick="alert(1)">link</a>')
    assert "onclick" not in out
    assert "alert" not in out


def test_blocks_javascript_scheme():
    out = sanitize_html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in out


def test_blocks_vbscript_scheme():
    out = sanitize_html('<a href="vbscript:msgbox(1)">x</a>')
    assert "vbscript:" not in out


def test_blocks_data_url_in_img():
    out = sanitize_html('<img src="data:text/html,<script>x</script>">')
    assert "data:" not in out


def test_style_allowlist_drops_position():
    """position/z-index/transform 등 클릭재킹 위험 속성 제거."""
    payload = '<div style="position:fixed;top:0;left:0;width:100vw;height:100vh;background:red;z-index:99999">x</div>'
    out = sanitize_html(payload)
    assert "position" not in out
    assert "z-index" not in out
    assert "100vw" not in out


def test_style_allowlist_keeps_color():
    out = sanitize_html('<span style="color: red">x</span>')
    assert "color: red" in out


def test_style_blocks_expression_value():
    """expression()은 IE legacy XSS이지만 방어적 차단."""
    out = sanitize_html('<span style="color: expression(alert(1))">x</span>')
    assert "expression" not in out


def test_style_blocks_url_value():
    out = sanitize_html('<span style="background-color: url(javascript:alert(1))">x</span>')
    assert "javascript" not in out
    assert "url(" not in out


def test_target_blank_gets_rel_noopener():
    """tabnabbing 방어 — target=_blank에 rel=noopener noreferrer 자동 부착."""
    out = sanitize_html('<a href="https://example.com" target="_blank">x</a>')
    assert 'rel="noopener noreferrer"' in out


def test_target_blank_keeps_existing_rel():
    out = sanitize_html('<a href="https://example.com" target="_blank" rel="custom">x</a>')
    # 기존 rel 보존 (rel은 allowed attr)
    assert 'rel="custom"' in out


def test_target_self_no_rel_added():
    out = sanitize_html('<a href="https://example.com" target="_self">x</a>')
    assert "noopener" not in out


def test_strips_disallowed_tags():
    out = sanitize_html("<iframe src='evil'></iframe><object></object>")
    assert "<iframe" not in out
    assert "<object" not in out


def test_keeps_allowed_inline_formatting():
    out = sanitize_html("<p><strong>bold</strong> <em>italic</em></p>")
    assert "<strong>" in out
    assert "<em>" in out


def test_handles_empty_input():
    assert sanitize_html("") == ""
    assert sanitize_html("   ") == ""
    assert sanitize_html(None or "") == ""


def test_strips_html_comments():
    out = sanitize_html("<p>x</p><!-- [if IE]><script>x</script><![endif] -->")
    assert "<!--" not in out
    assert "<script" not in out


def test_long_style_value_rejected():
    """매우 긴 style 값은 CSS injection payload 방어로 차단."""
    long_val = "color: " + "a" * 500
    out = sanitize_html(f'<span style="{long_val}">x</span>')
    assert len(out) < 600  # payload 그대로 통과되지 않음
