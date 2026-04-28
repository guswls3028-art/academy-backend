"""
업로드 이미지 검증 SSOT.

클라이언트 Content-Type 헤더만 신뢰하면 위장 가능 (HTML/스크립트를 image/png 로 업로드).
실제 파일 시작 바이트(매직 넘버)를 확인해 진짜 이미지인지 판정한다.
"""
from __future__ import annotations

# 알려진 이미지 매직 헤더. (offset, signature, label)
_IMAGE_SIGNATURES = (
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpeg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"BM", "bmp"),
    (8, b"WEBP", "webp"),  # RIFF....WEBP
)


def detect_image_kind(head: bytes) -> str | None:
    """첫 ~32바이트로 이미지 종류 판정. 알 수 없으면 None."""
    for offset, sig, label in _IMAGE_SIGNATURES:
        if len(head) >= offset + len(sig) and head[offset : offset + len(sig)] == sig:
            return label
    return None


def is_real_image(file_obj, *, max_read: int = 64) -> bool:
    """
    UploadedFile 류(read+seek 지원)에서 매직 헤더로 진짜 이미지인지 검증.
    검사 후 파일 포인터를 0으로 되돌려 후속 업로드가 영향을 받지 않도록 한다.
    """
    try:
        head = file_obj.read(max_read)
    except Exception:
        return False
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass
    return detect_image_kind(head or b"") is not None
