# Video order integrity runbook

Use this runbook before deploying
`video.0019_video_order_and_folder_uniqueness`. The migration is the Phase B
database guard; the repair command must run on the Phase A image while the
database is still at `video.0018`.

## 1. Read-only plan

Run one exact tenant at a time and retain the complete output. An unscoped run
checks every session and folder owned by that tenant. The command never mutates
without `--execute`.

```powershell
pwsh scripts/v1/run-api-management-remote.ps1 `
  -Command 'repair_video_order_duplicates --tenant <tenant-code>'
```

Review `changed_rows`, affected session/folder counts, each order transition,
and the SHA-256 plan checksum. Re-run if the candidate set changes.

## 2. Snapshot and guarded repair

Create a manual snapshot of the SSOT RDS instance and wait for it to become
`available`. On the digest-pinned API host, execute against the running
container so the exclusive backup file survives long enough to be copied to
the host.

```bash
sudo install -d -m 700 /opt/academy-backups/video-order
sudo docker exec academy-api python manage.py repair_video_order_duplicates \
  --tenant '<tenant-code>' \
  --execute \
  --confirm '<tenant-code>' \
  --expected-checksum '<reviewed-sha256>' \
  --backup-file '/tmp/video-order-<tenant-code>-<UTC>.json'
sudo docker cp \
  academy-api:/tmp/video-order-<tenant-code>-<UTC>.json \
  /opt/academy-backups/video-order/video-order-<tenant-code>-<UTC>.json
sudo chmod 600 /opt/academy-backups/video-order/video-order-<tenant-code>-<UTC>.json
sha256sum /opt/academy-backups/video-order/video-order-<tenant-code>-<UTC>.json
```

Copy the host backup to the private operations bucket declared by
`operations.backupBucket` in `docs/ssot/params.yaml`, below a date-scoped
`video-order/` prefix. Record the object key, object metadata/checksum, host
SHA-256, RDS snapshot identifier, plan checksum, and command output.

## 3. Verify and seal

Re-run the read-only command for every repaired tenant. Each must report
`changed_rows=0`. Also verify all of the following are zero immediately before
Phase B:

- open `WorkRecord` duplicate groups by `(tenant_id, staff_id)`;
- active folder video order duplicate groups by `(tenant_id, folder_id, order)`;
- active session video order duplicate groups by `(tenant_id, session_id, order)`;
- root and child `VideoFolder` name duplicate groups.

Only then deploy `staffs.0007_unique_open_work_record` and
`video.0019_video_order_and_folder_uniqueness`. Both migrations lock their
tables and re-check the preconditions in the same transaction before creating
the constraints. A non-zero precondition aborts the deployment without adding
partial guards.

## 2026-07-14 production seal evidence

- Restorable RDS snapshot: `academy-db-enterprise-qa-pre-repair-20260714-0305`.
- `limglish`: 14 rows across 13 sessions repaired; plan SHA-256
  `eaa9abca14139a0dfc81026a8d831429ceba89236ae2dda5150a4ad8872efb57`.
  The encrypted archive object is
  `2026-07-14/video-order/video-order-limglish-20260714T0317KST.json`; host
  SHA-256 `585702d8be2c324f63c87238cf4b89322c72b6003c2263a782fb6f89270eebc4`.
- `tchul`: 51 rows across 13 sessions repaired; plan SHA-256
  `da54ff699c41827526461162b67ac1219c8235c55d29359579e02026b480839c`.
  The encrypted archive object is
  `2026-07-14/video-order/video-order-tchul-20260714T0318KST.json`; host
  SHA-256 `d8e7df40cc0b46b2aaeb67004c8a5652ddb48c70bfd4e75ad7abb823d3b4cae0`.
- Independent post-checks: both tenants report `changed_rows=0`; open work
  records, root/child folder names, tenantless folders, and observed
  cross-tenant folder/video/work-record relations all report zero violations.
