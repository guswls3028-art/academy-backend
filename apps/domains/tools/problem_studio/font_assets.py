from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from django.utils import timezone
from fontTools.ttLib import TTFont

from apps.domains.tools.problem_studio.models import ProblemStudioFontAsset
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    generate_presigned_get_url_storage,
    upload_fileobj_to_r2_storage,
)


MAX_FONT_BYTES = 32 * 1024 * 1024
MAX_PERSONAL_FONTS = 30
_FONT_SUFFIXES = {".ttf": "ttf", ".otf": "otf"}
_FONT_MAGIC = {
    b"\x00\x01\x00\x00": "ttf",
    b"true": "ttf",
    b"typ1": "ttf",
    b"OTTO": "otf",
}
_SAFE_DISPLAY_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ParsedFont:
    family_name: str
    subfamily_name: str
    full_name: str
    postscript_name: str
    font_revision: str
    file_format: str
    content_type: str
    size_bytes: int
    sha256: str
    glyph_count: int
    supports_hangul: bool
    supports_latin: bool
    fs_type: int
    embedding_permission: str
    no_subsetting: bool


def _font_name(font: TTFont, name_id: int) -> str:
    name_table = font["name"]
    preferred = [
        record
        for record in name_table.names
        if record.nameID == name_id and record.platformID == 3
    ]
    candidates = preferred or [record for record in name_table.names if record.nameID == name_id]
    for record in candidates:
        try:
            value = record.toUnicode().strip()
        except Exception:
            continue
        if value:
            return value
    return ""


def _embedding_permission(fs_type: int) -> str:
    if fs_type & 0x0002:
        return "restricted"
    if fs_type & 0x0004:
        return "preview_print"
    if fs_type & 0x0008:
        return "editable"
    return "installable"


def parse_font_upload(*, data: bytes, original_name: str) -> ParsedFont:
    if not data:
        raise ValueError("글꼴 파일이 비어 있습니다.")
    if len(data) > MAX_FONT_BYTES:
        raise ValueError("글꼴 파일은 32MB까지 올릴 수 있습니다.")

    suffix = Path(original_name).suffix.lower()
    expected_format = _FONT_SUFFIXES.get(suffix)
    if expected_format is None:
        raise ValueError("TTF 또는 OTF 글꼴만 올릴 수 있습니다.")
    actual_format = _FONT_MAGIC.get(data[:4])
    if actual_format is None:
        if data[:4] == b"ttcf":
            raise ValueError("여러 글꼴이 묶인 TTC 파일은 아직 지원하지 않습니다.")
        if data[:4] in {b"wOFF", b"wOF2"}:
            raise ValueError("웹폰트 대신 원본 TTF 또는 OTF 파일을 올려 주세요.")
        raise ValueError("올바른 TTF/OTF 글꼴 파일이 아닙니다.")
    if actual_format != expected_format:
        raise ValueError("파일 확장자와 실제 글꼴 형식이 다릅니다.")

    try:
        with TTFont(
            BytesIO(data),
            lazy=False,
            recalcBBoxes=False,
            recalcTimestamp=False,
        ) as font:
            if "fvar" in font:
                raise ValueError("가변 글꼴은 한글 호환성이 일정하지 않아 아직 지원하지 않습니다.")
            family_name = _font_name(font, 1)
            if not family_name:
                raise ValueError("글꼴 내부 이름을 확인할 수 없습니다.")
            subfamily_name = _font_name(font, 2)
            full_name = _font_name(font, 4)
            postscript_name = _font_name(font, 6)
            cmap = font.getBestCmap() or {}
            glyph_count = int(getattr(font.get("maxp"), "numGlyphs", 0) or 0)
            if glyph_count <= 0 or not cmap:
                raise ValueError("사용 가능한 글자가 없는 글꼴입니다.")
            fs_type = int(getattr(font.get("OS/2"), "fsType", 0) or 0)
            revision = float(getattr(font.get("head"), "fontRevision", 0.0) or 0.0)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("손상되었거나 지원하지 않는 글꼴 파일입니다.") from exc

    return ParsedFont(
        family_name=family_name[:160],
        subfamily_name=subfamily_name[:160],
        full_name=full_name[:200],
        postscript_name=postscript_name[:200],
        font_revision=f"{revision:.3f}".rstrip("0").rstrip("."),
        file_format=actual_format,
        content_type=("font/ttf" if actual_format == "ttf" else "font/otf"),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        glyph_count=glyph_count,
        supports_hangul=any(0xAC00 <= codepoint <= 0xD7A3 for codepoint in cmap),
        supports_latin=all(codepoint in cmap for codepoint in (ord("A"), ord("a"), ord("0"))),
        fs_type=fs_type,
        embedding_permission=_embedding_permission(fs_type),
        no_subsetting=bool(fs_type & 0x0100),
    )


def _clean_display_name(value: Any, fallback: str) -> str:
    cleaned = _SAFE_DISPLAY_RE.sub("", str(value or "")).strip()
    return (cleaned or fallback)[:160]


def create_personal_font_asset(
    *,
    tenant: Any,
    user: Any,
    upload: Any,
    display_name: Any,
    license_basis: Any,
    license_name: Any = "",
    license_url: Any = "",
    license_note: Any = "",
    rights_confirmed: bool,
    redistribution_allowed: bool = False,
) -> ProblemStudioFontAsset:
    if not rights_confirmed:
        raise ValueError("글꼴을 문서 생성과 현재 사용자 PC에서 사용할 권리가 있는지 확인해 주세요.")

    allowed_license_basis = {
        choice
        for choice, _label in ProblemStudioFontAsset.LicenseBasis.choices
    }
    normalized_basis = str(license_basis or "").strip()
    if normalized_basis not in allowed_license_basis:
        raise ValueError("글꼴 사용 권한의 근거를 선택해 주세요.")
    if ProblemStudioFontAsset.objects.filter(
        tenant=tenant,
        uploaded_by=user,
        status=ProblemStudioFontAsset.Status.READY,
    ).count() >= MAX_PERSONAL_FONTS:
        raise ValueError(f"내 글꼴은 최대 {MAX_PERSONAL_FONTS}개까지 보관할 수 있습니다.")

    original_name = Path(str(getattr(upload, "name", "") or "font")).name[:255]
    data = upload.read(MAX_FONT_BYTES + 1)
    if hasattr(upload, "seek"):
        upload.seek(0)
    parsed = parse_font_upload(data=data, original_name=original_name)

    existing = ProblemStudioFontAsset.objects.filter(
        tenant=tenant,
        uploaded_by=user,
        sha256=parsed.sha256,
    ).first()
    if existing is not None:
        if existing.status == ProblemStudioFontAsset.Status.DISABLED:
            raise ValueError("이전에 삭제한 동일 글꼴입니다. 다른 파일이 필요하면 새 버전을 올려 주세요.")
        raise ValueError("이미 내 글꼴에 등록된 파일입니다.")

    asset_id = uuid.uuid4()
    extension = parsed.file_format
    r2_key = (
        f"tenants/{tenant.id}/tools/problem-studio/fonts/"
        f"{asset_id}/{parsed.sha256[:16]}.{extension}"
    )
    upload_fileobj_to_r2_storage(
        fileobj=BytesIO(data),
        key=r2_key,
        content_type=parsed.content_type,
    )
    try:
        return ProblemStudioFontAsset.objects.create(
            id=asset_id,
            tenant=tenant,
            uploaded_by=user,
            display_name=_clean_display_name(display_name, parsed.full_name or parsed.family_name),
            original_name=original_name,
            r2_key=r2_key,
            license_basis=normalized_basis,
            license_name=_clean_display_name(license_name, "")[:160],
            license_url=str(license_url or "").strip()[:500],
            license_note=str(license_note or "").strip()[:2000],
            rights_confirmed_at=timezone.now(),
            redistribution_allowed=bool(redistribution_allowed),
            **asdict(parsed),
        )
    except Exception:
        try:
            delete_object_r2_storage(key=r2_key)
        except Exception:
            pass
        raise


def font_asset_download_url(asset: ProblemStudioFontAsset, *, expires_in: int = 300) -> str:
    expected_prefix = f"tenants/{asset.tenant_id}/tools/problem-studio/fonts/{asset.id}/"
    if not asset.r2_key.startswith(expected_prefix):
        raise ValueError("글꼴 저장 경로가 올바르지 않습니다.")
    return generate_presigned_get_url_storage(
        key=asset.r2_key,
        expires_in=expires_in,
        filename=asset.original_name,
        content_type=asset.content_type,
    )


def delete_font_asset_file(*, tenant_id: Any, asset_id: Any, r2_key: str) -> None:
    expected_prefix = f"tenants/{tenant_id}/tools/problem-studio/fonts/{asset_id}/"
    if not r2_key.startswith(expected_prefix):
        raise ValueError("글꼴 저장 경로가 올바르지 않습니다.")
    delete_object_r2_storage(key=r2_key)


def serialize_font_asset(
    asset: ProblemStudioFontAsset,
    *,
    include_download_url: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": str(asset.id),
        "display_name": asset.display_name,
        "family_name": asset.family_name,
        "subfamily_name": asset.subfamily_name,
        "full_name": asset.full_name,
        "original_name": asset.original_name,
        "file_format": asset.file_format,
        "size_bytes": asset.size_bytes,
        "sha256": asset.sha256,
        "supports_hangul": asset.supports_hangul,
        "supports_latin": asset.supports_latin,
        "embedding_permission": asset.embedding_permission,
        "redistribution_allowed": asset.redistribution_allowed,
        "license_basis": asset.license_basis,
        "status": asset.status,
    }
    if include_download_url:
        payload["preview_url"] = font_asset_download_url(asset, expires_in=300)
    return payload
