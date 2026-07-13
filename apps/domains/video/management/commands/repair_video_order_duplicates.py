"""Plan, repair, and roll back duplicate active video orders safely."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from academy.adapters.db.django import repositories_video as video_repo
from apps.core.models import Tenant
from apps.domains.video.models import Video


def _rows_checksum(rows: list[dict]) -> str:
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _collection_key(video: Video) -> tuple[str, int] | None:
    """Folder owns ordering when present; otherwise the legacy session does."""
    if video.folder_id is not None:
        return "folder", video.folder_id
    if video.session_id is not None:
        return "session", video.session_id
    return None


def _build_contiguous_plan(videos: list[Video]) -> list[dict]:
    """Preserve deterministic playlist order and normalize duplicate collections."""
    by_collection: dict[tuple[str, int], list[Video]] = defaultdict(list)
    for video in videos:
        key = _collection_key(video)
        if key is not None:
            by_collection[key].append(video)

    plan: list[dict] = []
    for collection_type, collection_id in sorted(by_collection):
        collection_videos = by_collection[(collection_type, collection_id)]
        existing_orders = [video.order for video in collection_videos]
        if len(existing_orders) == len(set(existing_orders)):
            continue
        stable_order = sorted(
            collection_videos,
            key=lambda row: (row.order, row.id),
        )
        for new_order, video in enumerate(stable_order, start=1):
            if video.order != new_order:
                plan.append(
                    {
                        "video_id": video.id,
                        "collection_type": collection_type,
                        "collection_id": collection_id,
                        "session_id": video.session_id,
                        "folder_id": video.folder_id,
                        "old_order": video.order,
                        "new_order": new_order,
                    }
                )
    return plan


def _duplicate_groups(
    *,
    tenant_id: int,
    session_ids: list[int],
    folder_ids: list[int],
) -> int:
    session_groups = (
        Video.objects.filter(
            tenant_id=tenant_id,
            folder_id__isnull=True,
            session_id__in=session_ids,
        )
        .values("session_id", "order")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .count()
    )
    folder_groups = (
        Video.objects.filter(
            tenant_id=tenant_id,
            folder_id__in=folder_ids,
        )
        .values("folder_id", "order")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .count()
    )
    return session_groups + folder_groups


def _bulk_reassign_orders(
    videos: list[Video],
    *,
    final_order_by_id: dict[int, int],
) -> None:
    """Apply swaps safely under immediate PostgreSQL uniqueness checks."""
    if not videos:
        return
    temporary_base = (
        max(
            [video.order for video in videos]
            + list(final_order_by_id.values())
        )
        + len(videos)
        + 1
    )
    for index, video in enumerate(videos, start=1):
        video.order = temporary_base + index
    Video.objects.bulk_update(videos, ["order"], batch_size=500)
    for video in videos:
        video.order = final_order_by_id[video.id]
    Video.objects.bulk_update(videos, ["order"], batch_size=500)


class Command(BaseCommand):
    help = (
        "Dry-run by default. Contiguously renumber duplicate video collections "
        "(folder first, otherwise session), with a checksummed backup and guarded rollback."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Exact Tenant.code")
        parser.add_argument(
            "--session-id",
            action="append",
            type=int,
            dest="session_ids",
            help="Optional session scope; repeat for multiple sessions",
        )
        parser.add_argument(
            "--folder-id",
            action="append",
            type=int,
            dest="folder_ids",
            help="Optional folder scope; repeat for multiple folders",
        )
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--rollback", help="Backup JSON produced by this command")
        parser.add_argument("--backup-file", help="Required for --execute; optional for dry-run")
        parser.add_argument(
            "--expected-checksum",
            help="Required for --execute; checksum printed by a reviewed dry-run",
        )
        parser.add_argument(
            "--confirm",
            help="For a mutation, repeat the exact tenant code",
        )

    def handle(self, *args, **options):
        tenant_code = options["tenant"]
        try:
            tenant = Tenant.objects.get(code=tenant_code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"tenant_not_found:{tenant_code}") from exc

        execute = bool(options["execute"])
        rollback_path = options.get("rollback")
        if execute and rollback_path:
            raise CommandError("choose_exactly_one:--execute_or_--rollback")
        if (execute or rollback_path) and options.get("confirm") != tenant_code:
            raise CommandError("confirmation_required:--confirm must equal --tenant")

        if rollback_path:
            self._rollback(tenant=tenant, path=Path(rollback_path))
            return

        session_ids = sorted(set(options.get("session_ids") or []))
        folder_ids = sorted(set(options.get("folder_ids") or []))
        backup_file = options.get("backup_file")
        if execute and not backup_file:
            raise CommandError("--backup-file is required with --execute")
        if execute and not options.get("expected_checksum"):
            raise CommandError("--expected-checksum is required with --execute")
        if backup_file and not Path(backup_file).expanduser().is_absolute():
            raise CommandError("--backup-file must be an absolute durable path")

        if execute:
            backup_path = Path(backup_file).expanduser().resolve()
            if backup_path.exists():
                raise CommandError("backup_file_already_exists:refusing_to_overwrite")
            with transaction.atomic():
                plan = self._locked_plan(
                    tenant=tenant,
                    session_ids=session_ids,
                    folder_ids=folder_ids,
                )
                payload = self._payload(tenant=tenant, rows=plan, state="planned")
                if payload["rows_checksum"] != options["expected_checksum"]:
                    raise CommandError(
                        "plan_checksum_mismatch:rerun_dry_run_and_review_before_execute"
                    )
                self._write_backup(backup_path, payload, exclusive=True)
                self._apply_plan(tenant=tenant, rows=plan)
                affected_sessions = sorted(
                    {
                        row["session_id"]
                        for row in plan
                        if row["collection_type"] == "session"
                    }
                )
                affected_folders = sorted(
                    {row["folder_id"] for row in plan if row["folder_id"] is not None}
                )
                remaining = _duplicate_groups(
                    tenant_id=tenant.id,
                    session_ids=affected_sessions,
                    folder_ids=affected_folders,
                )
                if remaining:
                    raise CommandError(
                        f"post_repair_duplicate_groups:{remaining};transaction_rolled_back"
                    )
            payload["state"] = "applied"
            payload["applied_at"] = timezone.now().isoformat()
            self._replace_backup(backup_path, payload)
            self.stdout.write(
                self.style.SUCCESS(
                    f"applied tenant={tenant.code} changed_rows={len(plan)} "
                    f"sessions={len(payload['affected_session_ids'])} "
                    f"folders={len(payload['affected_folder_ids'])} duplicate_groups=0"
                )
            )
            return

        plan = self._unlocked_plan(
            tenant=tenant,
            session_ids=session_ids,
            folder_ids=folder_ids,
        )
        payload = self._payload(tenant=tenant, rows=plan, state="dry_run")
        if backup_file:
            self._write_backup(Path(backup_file), payload, exclusive=True)
        self.stdout.write(
            f"dry_run tenant={tenant.code} changed_rows={len(plan)} "
            f"sessions={len(payload['affected_session_ids'])} "
            f"folders={len(payload['affected_folder_ids'])} "
            f"checksum={payload['rows_checksum']}"
        )
        for row in plan:
            self.stdout.write(
                "  {collection_type}={collection_id} video={video_id} "
                "order={old_order}->{new_order}".format(**row)
            )

    def _base_queryset(
        self,
        *,
        tenant: Tenant,
        session_ids: list[int],
        folder_ids: list[int],
    ):
        queryset = Video.objects.filter(tenant=tenant).filter(
            Q(folder_id__isnull=False) | Q(session_id__isnull=False)
        )
        if session_ids or folder_ids:
            scope = Q()
            if session_ids:
                scope |= Q(folder_id__isnull=True, session_id__in=session_ids)
            if folder_ids:
                scope |= Q(folder_id__in=folder_ids)
            queryset = queryset.filter(scope)
        return queryset.order_by("folder_id", "session_id", "order", "title", "id")

    def _unlocked_plan(
        self,
        *,
        tenant: Tenant,
        session_ids: list[int],
        folder_ids: list[int],
    ) -> list[dict]:
        return _build_contiguous_plan(
            list(
                self._base_queryset(
                    tenant=tenant,
                    session_ids=session_ids,
                    folder_ids=folder_ids,
                )
            )
        )

    def _locked_plan(
        self,
        *,
        tenant: Tenant,
        session_ids: list[int],
        folder_ids: list[int],
    ) -> list[dict]:
        scoped = self._base_queryset(
            tenant=tenant,
            session_ids=session_ids,
            folder_ids=folder_ids,
        )
        target_session_ids = list(
            scoped.filter(folder_id__isnull=True)
            .values_list("session_id", flat=True)
            .distinct()
        )
        target_folder_ids = list(
            scoped.filter(folder_id__isnull=False)
            .values_list("folder_id", flat=True)
            .distinct()
        )
        video_repo.lock_sessions_by_ids(target_session_ids)
        video_repo.lock_video_folders_by_ids(target_folder_ids)
        videos = list(scoped.select_for_update())
        return _build_contiguous_plan(videos)

    def _payload(self, *, tenant: Tenant, rows: list[dict], state: str) -> dict:
        return {
            "schema_version": 1,
            "state": state,
            "mutation_backup": state == "planned",
            "created_at": timezone.now().isoformat(),
            "tenant_id": tenant.id,
            "tenant_code": tenant.code,
            "affected_session_ids": sorted(
                {
                    row["session_id"]
                    for row in rows
                    if row["collection_type"] == "session"
                }
            ),
            "affected_folder_ids": sorted(
                {
                    row["folder_id"]
                    for row in rows
                    if row["collection_type"] == "folder"
                }
            ),
            "changed_rows": len(rows),
            "rows_checksum": _rows_checksum(rows),
            "rows": rows,
        }

    def _write_backup(self, path: Path, payload: dict, *, exclusive: bool) -> None:
        resolved = path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            with resolved.open("x" if exclusive else "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise CommandError("backup_file_already_exists:refusing_to_overwrite") from exc
        self._fsync_parent(resolved)

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        """Best-effort directory durability; unsupported on some Windows FSes."""
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)

    def _replace_backup(self, path: Path, payload: dict) -> None:
        """Mark this run applied without accepting an unrelated pre-existing file."""
        resolved = path.expanduser().resolve()
        try:
            existing = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"backup_state_update_failed:{exc}") from exc
        if (
            existing.get("state") != "planned"
            or existing.get("rows_checksum") != payload.get("rows_checksum")
            or existing.get("tenant_id") != payload.get("tenant_id")
        ):
            raise CommandError("backup_state_update_refused:content_changed")
        temporary = resolved.with_name(
            f".{resolved.name}.{uuid4().hex}.tmp"
        )
        try:
            self._write_backup(temporary, payload, exclusive=True)
            os.replace(temporary, resolved)
            self._fsync_parent(resolved)
        except OSError as exc:
            # os.replace is atomic: when it fails, the only accepted planned
            # rollback artifact remains intact and parseable.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CommandError(f"backup_state_update_failed:{exc}") from exc

    def _apply_plan(self, *, tenant: Tenant, rows: list[dict]) -> None:
        if not rows:
            return
        by_id = {row["video_id"]: row for row in rows}
        videos = list(
            Video.objects.select_for_update()
            .filter(tenant=tenant, id__in=by_id)
            .order_by("id")
        )
        if len(videos) != len(rows):
            raise CommandError("plan_stale:video_missing")
        final_order_by_id = {}
        for video in videos:
            row = by_id[video.id]
            if (
                _collection_key(video)
                != (row["collection_type"], row["collection_id"])
                or video.order != row["old_order"]
            ):
                raise CommandError(f"plan_stale:video={video.id}")
            final_order_by_id[video.id] = row["new_order"]
        _bulk_reassign_orders(videos, final_order_by_id=final_order_by_id)

    def _rollback(self, *, tenant: Tenant, path: Path) -> None:
        try:
            payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"invalid_backup:{exc}") from exc

        rows = payload.get("rows")
        if not isinstance(rows, list) or payload.get("schema_version") != 1:
            raise CommandError("invalid_backup_schema")
        if payload.get("state") not in ("applied", "planned") or not payload.get(
            "mutation_backup"
        ):
            raise CommandError("rollback_requires_applied_backup")
        if payload.get("tenant_id") != tenant.id or payload.get("tenant_code") != tenant.code:
            raise CommandError("backup_tenant_mismatch")
        if payload.get("rows_checksum") != _rows_checksum(rows):
            raise CommandError("backup_checksum_mismatch")

        by_id = {row["video_id"]: row for row in rows}
        with transaction.atomic():
            session_ids = sorted(
                {
                    row["session_id"]
                    for row in rows
                    if row["collection_type"] == "session"
                }
            )
            folder_ids = sorted(
                {
                    row["folder_id"]
                    for row in rows
                    if row["collection_type"] == "folder"
                }
            )
            video_repo.lock_sessions_by_ids(session_ids)
            video_repo.lock_video_folders_by_ids(folder_ids)
            videos = list(
                Video.objects.select_for_update()
                .filter(tenant=tenant, id__in=by_id)
                .order_by("id")
            )
            if len(videos) != len(rows):
                raise CommandError("rollback_stale:video_missing")
            final_order_by_id = {}
            for video in videos:
                row = by_id[video.id]
                if (
                    _collection_key(video)
                    != (row["collection_type"], row["collection_id"])
                    or video.order != row["new_order"]
                ):
                    raise CommandError(f"rollback_stale:video={video.id}")
                final_order_by_id[video.id] = row["old_order"]
            _bulk_reassign_orders(
                videos,
                final_order_by_id=final_order_by_id,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"rolled_back tenant={tenant.code} restored_rows={len(rows)}"
            )
        )
