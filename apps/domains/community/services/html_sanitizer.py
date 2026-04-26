# PATH: apps/domains/community/services/html_sanitizer.py
# 서버사이드 HTML sanitization — 허용 태그 + style 화이트리스트 + 안전 링크
# 의존성 없이 Python 표준 라이브러리만 사용

import re
from html.parser import HTMLParser
from html import escape

ALLOWED_TAGS = frozenset({
    "p", "br", "strong", "b", "em", "i", "u", "s", "del",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "a", "img",
    "blockquote", "code", "pre",
    "div", "span",
    "table", "thead", "tbody", "tr", "th", "td",
})

ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "span": {"style"},
    "div": {"style"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# style 속성 화이트리스트 — 텍스트 표현/정렬만 허용
# (position/z-index/transform 등 클릭재킹 위험 속성 차단)
ALLOWED_STYLE_PROPS = frozenset({
    "color", "background-color",
    "font-weight", "font-style", "font-size", "font-family",
    "text-align", "text-decoration",
    "line-height",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
})

# CSS value에 위험 패턴 차단 — javascript:, expression(), url(...), CSS variable injection 등
_DANGEROUS_CSS_VALUE = re.compile(
    r"(?:javascript:|expression\s*\(|behavior\s*:|@import|url\s*\(|--|\\)",
    re.IGNORECASE,
)

# Block dangerous URL schemes
DANGEROUS_SCHEMES = re.compile(r"^\s*(javascript|vbscript|data):", re.IGNORECASE)


def _sanitize_style(value: str) -> str:
    """style 속성 값을 파싱해 화이트리스트 속성만 통과."""
    if not value:
        return ""
    safe_decls = []
    for decl in value.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        if prop not in ALLOWED_STYLE_PROPS:
            continue
        if _DANGEROUS_CSS_VALUE.search(val):
            continue
        # 매우 긴 값 차단 (CSS injection payload 방어)
        if len(val) > 200:
            continue
        safe_decls.append(f"{prop}: {val}")
    return "; ".join(safe_decls)


class _SanitizingParser(HTMLParser):
    """Allowlist-based HTML sanitizer using standard library."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        safe_attrs = []
        has_target_blank = False
        for name, value in attrs:
            if name not in allowed:
                continue
            # Block event handlers (방어적 — allowed에 on*이 없지만 이중 방어)
            if name.startswith("on"):
                continue
            # Block dangerous schemes in href/src
            if name in ("href", "src") and value and DANGEROUS_SCHEMES.match(value):
                continue
            if name == "style":
                value = _sanitize_style(value or "")
                if not value:
                    continue
            if name == "target" and (value or "").lower() == "_blank":
                has_target_blank = True
            safe_attrs.append(f'{name}="{escape(value or "", quote=True)}"')
        # target=_blank 시 rel="noopener noreferrer" 자동 부착 (tabnabbing 방어)
        if tag == "a" and has_target_blank:
            has_rel = any(a.startswith('rel=') for a in safe_attrs)
            if not has_rel:
                safe_attrs.append('rel="noopener noreferrer"')
        attr_str = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        if tag == "br" or tag == "img":
            self.result.append(f"<{tag}{attr_str} />")
        else:
            self.result.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS and tag not in ("br", "img"):
            self.result.append(f"</{tag}>")

    def handle_data(self, data):
        self.result.append(escape(data))

    def handle_entityref(self, name):
        self.result.append(f"&{name};")

    def handle_charref(self, name):
        self.result.append(f"&#{name};")

    # 주석은 무시 (IE conditional 등 historical XSS 방지)
    def handle_comment(self, _data):
        return


def sanitize_html(html: str) -> str:
    """
    Sanitize HTML content for safe storage.
    - 비허용 태그/속성 제거
    - style 속성은 화이트리스트 속성만 통과 (position/z-index 등 차단)
    - javascript:, vbscript:, data: URL 스킴 차단
    - target=_blank 링크에 rel="noopener noreferrer" 자동 부착
    - on* 이벤트 핸들러 제거
    """
    if not html or not html.strip():
        return ""
    parser = _SanitizingParser()
    parser.feed(html)
    return "".join(parser.result)
