from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from apps.domains.tools.problem_studio.models import (
    ProblemStudioDocumentStyle,
    ProblemStudioFontAsset,
)


BUILTIN_FONTS = (
    {"key": "hamchorom-batang", "label": "함초롬바탕", "family_name": "함초롬바탕"},
    {"key": "hamchorom-dotum", "label": "함초롬돋움", "family_name": "함초롬돋움"},
    {"key": "malgun-gothic", "label": "맑은 고딕", "family_name": "맑은 고딕"},
    {"key": "batang", "label": "바탕", "family_name": "바탕"},
    {"key": "dotum", "label": "돋움", "family_name": "돋움"},
    {"key": "gulim", "label": "굴림", "family_name": "굴림"},
)
_BUILTIN_BY_KEY = {item["key"]: item for item in BUILTIN_FONTS}
DEFAULT_TITLE_FONT = "hamchorom-dotum"
DEFAULT_BODY_FONT = "hamchorom-batang"


def _decimal_setting(value: Any, *, name: str, minimum: str, maximum: str) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name} 값을 확인해 주세요.") from exc
    lower = Decimal(minimum)
    upper = Decimal(maximum)
    if normalized < lower or normalized > upper:
        raise ValueError(f"{name}은 {lower:g}~{upper:g} 범위에서 선택해 주세요.")
    return normalized.quantize(Decimal("0.1"))


def _integer_setting(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 값을 확인해 주세요.") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{name}은 {minimum}~{maximum} 범위에서 선택해 주세요.")
    return normalized


def _asset_for_selection(selection: str, *, tenant: Any, user: Any) -> ProblemStudioFontAsset | None:
    if selection.startswith("builtin:"):
        key = selection.removeprefix("builtin:")
        if key not in _BUILTIN_BY_KEY:
            raise ValueError("선택한 기본 글꼴을 찾을 수 없습니다.")
        return None
    if not selection.startswith("asset:"):
        raise ValueError("글꼴 선택값이 올바르지 않습니다.")
    try:
        asset_id = UUID(selection.removeprefix("asset:"))
    except ValueError as exc:
        raise ValueError("선택한 내 글꼴을 찾을 수 없습니다.") from exc
    asset = ProblemStudioFontAsset.objects.filter(
        id=asset_id,
        tenant=tenant,
        uploaded_by=user,
        status=ProblemStudioFontAsset.Status.READY,
    ).first()
    if asset is None:
        raise ValueError("선택한 내 글꼴을 사용할 수 없습니다.")
    return asset


def _font_selection(
    *,
    key: str,
    asset: ProblemStudioFontAsset | None,
) -> str:
    return f"asset:{asset.id}" if asset is not None else f"builtin:{key}"


def _preference_values(preference: ProblemStudioDocumentStyle | None) -> dict[str, Any]:
    if preference is None:
        return {
            "title_font": f"builtin:{DEFAULT_TITLE_FONT}",
            "body_font": f"builtin:{DEFAULT_BODY_FONT}",
            "title_size_pt": 20,
            "body_size_pt": 10.5,
            "line_spacing_percent": 155,
            "question_spacing_pt": 10,
        }
    return {
        "title_font": _font_selection(
            key=preference.title_font_key,
            asset=preference.title_font_asset
            if preference.title_font_asset
            and preference.title_font_asset.status == ProblemStudioFontAsset.Status.READY
            else None,
        ),
        "body_font": _font_selection(
            key=preference.body_font_key,
            asset=preference.body_font_asset
            if preference.body_font_asset
            and preference.body_font_asset.status == ProblemStudioFontAsset.Status.READY
            else None,
        ),
        "title_size_pt": float(preference.title_size_pt),
        "body_size_pt": float(preference.body_size_pt),
        "line_spacing_percent": preference.line_spacing_percent,
        "question_spacing_pt": float(preference.question_spacing_pt),
    }


def serialize_document_style_preference(*, tenant: Any, user: Any) -> dict[str, Any]:
    preference = ProblemStudioDocumentStyle.objects.filter(
        tenant=tenant,
        user=user,
    ).select_related("title_font_asset", "body_font_asset").first()
    return _preference_values(preference)


def _resolved_font(
    selection: str,
    *,
    tenant: Any,
    user: Any,
) -> tuple[str, ProblemStudioFontAsset | None, dict[str, Any]]:
    asset = _asset_for_selection(selection, tenant=tenant, user=user)
    if asset is not None:
        return "", asset, {
            "source": "asset",
            "family_name": asset.family_name,
            "asset": {
                "id": str(asset.id),
                "family_name": asset.family_name,
                "original_name": asset.original_name,
                "r2_key": asset.r2_key,
                "size_bytes": asset.size_bytes,
                "sha256": asset.sha256,
                "content_type": asset.content_type,
            },
        }
    key = selection.removeprefix("builtin:")
    builtin = _BUILTIN_BY_KEY[key]
    return key, None, {
        "source": "builtin",
        "key": key,
        "family_name": builtin["family_name"],
        "asset": None,
    }


def resolve_document_style_payload(
    payload: dict[str, Any],
    *,
    tenant: Any,
    user: Any,
) -> dict[str, Any]:
    preference = ProblemStudioDocumentStyle.objects.filter(
        tenant=tenant,
        user=user,
    ).select_related("title_font_asset", "body_font_asset").first()
    values = _preference_values(preference)
    requested = payload.get("document_style")
    if isinstance(requested, dict):
        values.update({
            key: requested[key]
            for key in values
            if key in requested
        })

    title_key, title_asset, resolved_title = _resolved_font(
        str(values["title_font"]),
        tenant=tenant,
        user=user,
    )
    body_key, body_asset, resolved_body = _resolved_font(
        str(values["body_font"]),
        tenant=tenant,
        user=user,
    )
    title_size = _decimal_setting(
        values["title_size_pt"], name="제목 크기", minimum="14", maximum="32"
    )
    body_size = _decimal_setting(
        values["body_size_pt"], name="본문 크기", minimum="8", maximum="18"
    )
    line_spacing = _integer_setting(
        values["line_spacing_percent"], name="줄 간격", minimum=120, maximum=220
    )
    question_spacing = _decimal_setting(
        values["question_spacing_pt"], name="문항 간격", minimum="0", maximum="24"
    )

    resolved = {
        "schema": "problem-studio-document-style/v1",
        "title_font": resolved_title,
        "body_font": resolved_body,
        "title_size_pt": float(title_size),
        "body_size_pt": float(body_size),
        "line_spacing_percent": line_spacing,
        "question_spacing_pt": float(question_spacing),
        "native_equations": True,
        "requested_by_user_id": str(user.id),
    }
    output = dict(payload)
    output["document_style"] = {
        "title_font": _font_selection(key=title_key, asset=title_asset),
        "body_font": _font_selection(key=body_key, asset=body_asset),
        "title_size_pt": float(title_size),
        "body_size_pt": float(body_size),
        "line_spacing_percent": line_spacing,
        "question_spacing_pt": float(question_spacing),
    }
    output["_resolved_document_style"] = resolved
    return output


def save_document_style_preference(
    data: dict[str, Any],
    *,
    tenant: Any,
    user: Any,
) -> ProblemStudioDocumentStyle:
    payload = resolve_document_style_payload(
        {"document_style": data},
        tenant=tenant,
        user=user,
    )
    requested = payload["document_style"]
    title_asset = _asset_for_selection(requested["title_font"], tenant=tenant, user=user)
    body_asset = _asset_for_selection(requested["body_font"], tenant=tenant, user=user)
    title_key = (
        DEFAULT_TITLE_FONT
        if title_asset is not None
        else requested["title_font"].removeprefix("builtin:")
    )
    body_key = (
        DEFAULT_BODY_FONT
        if body_asset is not None
        else requested["body_font"].removeprefix("builtin:")
    )
    preference, _created = ProblemStudioDocumentStyle.objects.update_or_create(
        tenant=tenant,
        user=user,
        defaults={
            "title_font_key": title_key,
            "title_font_asset": title_asset,
            "body_font_key": body_key,
            "body_font_asset": body_asset,
            "title_size_pt": requested["title_size_pt"],
            "body_size_pt": requested["body_size_pt"],
            "line_spacing_percent": requested["line_spacing_percent"],
            "question_spacing_pt": requested["question_spacing_pt"],
        },
    )
    return preference


def revalidate_resolved_document_style(
    resolved: dict[str, Any] | None,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    if not isinstance(resolved, dict):
        return {
            "schema": "problem-studio-document-style/v1",
            "title_font": {
                "source": "builtin",
                "key": DEFAULT_TITLE_FONT,
                "family_name": _BUILTIN_BY_KEY[DEFAULT_TITLE_FONT]["family_name"],
                "asset": None,
            },
            "body_font": {
                "source": "builtin",
                "key": DEFAULT_BODY_FONT,
                "family_name": _BUILTIN_BY_KEY[DEFAULT_BODY_FONT]["family_name"],
                "asset": None,
            },
            "title_size_pt": 20.0,
            "body_size_pt": 10.5,
            "line_spacing_percent": 155,
            "question_spacing_pt": 10.0,
            "native_equations": True,
            "requested_by_user_id": user_id,
        }
    for field in ("title_font", "body_font"):
        font = resolved.get(field)
        if not isinstance(font, dict) or font.get("source") != "asset":
            continue
        snapshot = font.get("asset")
        if not isinstance(snapshot, dict):
            raise ValueError("문서 글꼴 정보가 올바르지 않습니다.")
        asset = ProblemStudioFontAsset.objects.filter(
            id=snapshot.get("id"),
            tenant_id=tenant_id,
            uploaded_by_id=user_id,
            status=ProblemStudioFontAsset.Status.READY,
            sha256=snapshot.get("sha256"),
        ).first()
        if asset is None or asset.r2_key != snapshot.get("r2_key"):
            raise ValueError("선택한 내 글꼴을 더 이상 사용할 수 없습니다.")
    return resolved


def custom_font_snapshots(resolved: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(resolved, dict):
        return []
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for field in ("title_font", "body_font"):
        font = resolved.get(field)
        snapshot = font.get("asset") if isinstance(font, dict) else None
        if not isinstance(snapshot, dict):
            continue
        asset_id = str(snapshot.get("id") or "")
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        output.append(dict(snapshot))
    return output
