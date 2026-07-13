from __future__ import annotations

from django.utils import timezone


def purge_expired_preview_tokens(
    *,
    batch_size: int = 500,
    max_batches: int | None = None,
) -> int:
    """Delete expired preview payloads in bounded batches."""
    from apps.domains.messaging.models import NotificationPreviewToken

    deleted = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        now = timezone.now()
        token_ids = list(
            NotificationPreviewToken.objects.filter(expires_at__lte=now)
            .order_by("expires_at", "id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not token_ids:
            break
        NotificationPreviewToken.objects.filter(
            id__in=token_ids,
            expires_at__lte=now,
        ).delete()
        deleted += len(token_ids)
        batches += 1
    return deleted
