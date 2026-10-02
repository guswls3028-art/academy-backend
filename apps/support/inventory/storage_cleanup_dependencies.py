"""Inventory uploads and deletion use the durable Storage cleanup boundary."""

from __future__ import annotations


def schedule_inventory_storage_cleanup(*, tenant_id: int, object_keys):
    from apps.domains.submissions.services.lifecycle import schedule_inventory_storage_cleanup as schedule

    return schedule(tenant_id=tenant_id, object_keys=object_keys)


def inventory_storage_cleanup_status(*, tenant_id: int, intent_ids):
    from apps.domains.submissions.services.lifecycle import inventory_storage_cleanup_status as status

    return status(tenant_id=tenant_id, intent_ids=intent_ids)


def ensure_storage_inventory_key_attachable(*, tenant_id: int, key: str) -> None:
    from apps.domains.submissions.services.lifecycle import (
        ensure_storage_inventory_key_attachable as _ensure,
    )

    _ensure(tenant_id=tenant_id, key=key)


def compensate_unattached_storage_object(
    *,
    tenant_id: int,
    key: str,
    uncertain_write: bool = False,
) -> str:
    from apps.domains.submissions.services.lifecycle import (
        compensate_unattached_storage_object as _compensate,
    )

    return _compensate(
        tenant_id=tenant_id,
        key=key,
        uncertain_write=uncertain_write,
    )


def uncertain_storage_write_settle_delay():
    from apps.domains.submissions.services.lifecycle import (
        UNCERTAIN_STORAGE_WRITE_SETTLE_DELAY,
    )

    return UNCERTAIN_STORAGE_WRITE_SETTLE_DELAY


def process_pending_storage_cleanup_intents():
    from apps.domains.submissions.services.lifecycle import (
        process_submission_storage_cleanup_intents,
    )

    return process_submission_storage_cleanup_intents()
