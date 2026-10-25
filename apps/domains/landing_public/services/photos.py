"""Durable public-community photo references; signed URLs are read-time values."""
import re
from urllib.parse import unquote, urlsplit

from django.conf import settings
from rest_framework import serializers


def photo_key(value, tenant_id):
    if not isinstance(value, str) or not tenant_id:
        return None
    key = value
    if value.startswith("https://"):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        endpoint = urlsplit(getattr(settings, "R2_ENDPOINT", ""))
        bucket = getattr(settings, "R2_ADMIN_BUCKET", "academy-admin")
        if parsed.username or parsed.password or parsed.fragment or not endpoint.netloc:
            return None
        if parsed.netloc == endpoint.netloc:
            prefix = f"/{bucket}/"
            if not parsed.path.startswith(prefix):
                return None
            key = unquote(parsed.path[len(prefix):])
        elif parsed.netloc == f"{bucket}.{endpoint.netloc}":
            key = unquote(parsed.path.lstrip("/"))
        else:
            return None
    pattern = rf"landing-public/reviews/{int(tenant_id)}/(?:[A-Za-z0-9]{{1,32}}|users/[1-9]\d*/[a-f0-9]{{64}})\.(?:png|jpg|jpeg|webp)"
    return key if re.fullmatch(pattern, key) else None


def validate_photos(value, *, tenant_id, user_id, existing=()):
    if not isinstance(value, list) or len(value) > 8:
        raise serializers.ValidationError("사진은 최대 8장까지 첨부할 수 있습니다.")
    if not tenant_id or not user_id:
        raise serializers.ValidationError("로그인한 학원 정보를 확인할 수 없습니다.")
    existing = existing if isinstance(existing, list) else []
    existing_keys = {photo_key(item, tenant_id) for item in existing}
    result = []
    for item in value:
        key = photo_key(item, tenant_id)
        if key:
            # Existing shared/legacy photos remain editable; new uploads belong
            # to the authenticated author, even when staff edits another post.
            if "/users/" in key and not key.startswith(f"landing-public/reviews/{tenant_id}/users/{user_id}/") and key not in existing_keys:
                raise serializers.ValidationError("이 계정에서 첨부한 사진만 추가할 수 있습니다.")
            reference = key
        elif isinstance(item, str) and item in existing and item.startswith("https://"):
            reference = item  # Preserve pre-existing external user-authored media.
        else:
            raise serializers.ValidationError("이 학원에 업로드한 사진을 선택해 주세요.")
        if reference not in result:
            result.append(reference)
    return result


def photo_urls(values, tenant_id):
    from apps.infrastructure.storage import r2

    result = []
    for value in values if isinstance(values, list) else []:
        key = photo_key(value, tenant_id)
        if key:
            result.append(r2.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7))
        elif isinstance(value, str) and value.startswith("https://"):
            # Never re-sign an unknown key or another tenant's storage object.
            endpoint_host = urlsplit(getattr(settings, "R2_ENDPOINT", "")).hostname
            try:
                host = urlsplit(value).hostname
            except ValueError:
                continue
            if host and endpoint_host and (host == endpoint_host or host.endswith(f".{endpoint_host}")):
                continue
            result.append(value)
    return result
