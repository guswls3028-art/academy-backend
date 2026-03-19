# PATH: apps/domains/community/services/html_sanitizer.py
# 서버사이드 HTML sanitization — 허용 태그 기반
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

# Block dangerous URL schemes
DANGEROUS_SCHEMES = re.compile(r"^\s*(javascript|vbscript|data):", re.IGNORECASE)


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
        for name, value in attrs:
            if name in allowed:
                # Block dangerous schemes in href/src
                if name in ("href", "src") and value and DANGEROUS_SCHEMES.match(value):
                    continue
                # Block event handlers
                if name.startswith("on"):
                    continue
                safe_attrs.append(f'{name}="{escape(value or "", quote=True)}"')
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


def sanitize_html(html: str) -> str:
    """
    Sanitize HTML content for safe storage.
    Strips disallowed tags, removes event handlers, blocks dangerous URLs.
    """
    if not html or not html.strip():
        return ""
    parser = _SanitizingParser()
    parser.feed(html)
    return "".join(parser.result)
