"""Conservative contact normalization for Excel/import boundaries, without DB access."""

import re


_RELATION = r"(?:학부모|보호자|아버지|어머니|아빠|엄마|부친|모친|부모|학생|본인|부|모|father|mother|parent|guardian)"
_LABEL = rf"(?:\(\s*{_RELATION}\s*\)|\[\s*{_RELATION}\s*\]|{_RELATION})"
_PREFIX = re.compile(rf"^{_LABEL}\s*:?\s*", re.IGNORECASE)
_SUFFIX = re.compile(rf"\s*{_LABEL}$", re.IGNORECASE)
_PHONE = re.compile(r"(?:010|10|\(010\))(?:\s*[-.]?\s*)[0-9]{4}(?:\s*[-.]?\s*)[0-9]{4}")
_WIDTH = str.maketrans({chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)})
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—―−"})


def normalize_excel_phone(value: object) -> str:
    """Return digits only for one unambiguous phone; preserve invalid input for errors.

    Never strip arbitrary text/digits or choose one of multiple phone numbers.
    Eight-digit legacy student identifiers remain eight digits for their caller.
    """
    original = "" if value is None else str(value).strip()
    text = original.translate(_WIDTH).translate(_DASHES)
    stripped, count = _PREFIX.subn("", text, count=1)
    if not count:
        stripped = _SUFFIX.sub("", text, count=1)
    text = stripped.strip()
    if not (_PHONE.fullmatch(text) or re.fullmatch(r"[0-9]{8}", text)):
        return original
    digits = re.sub(r"[^0-9]", "", text)
    return "0" + digits if len(digits) == 10 and digits.startswith("10") else digits
