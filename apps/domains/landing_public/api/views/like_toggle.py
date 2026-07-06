from django.db import transaction
from django.db.utils import IntegrityError

from ...models import PublicPostLike


def toggle_public_like(*, tenant, user, target_kind: str, target_id: int) -> bool:
    """Race-safe toggle for a user's public like on a target."""
    lookup = {
        "tenant": tenant,
        "user": user,
        "target_kind": target_kind,
        "target_id": target_id,
    }
    existing = PublicPostLike.objects.filter(**lookup).first()
    if existing:
        existing.delete()
        return False

    try:
        with transaction.atomic():
            PublicPostLike.objects.create(**lookup)
    except IntegrityError:
        # Another request created the same like after our existence check.
        # Resolve the race as "liked" instead of surfacing a 500.
        return True
    return True
