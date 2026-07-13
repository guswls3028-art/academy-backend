# Tenant access integrity runbook

`audit_tenant_access` checks the authorization/profile invariants that must hold
before a release:

- active user with an active primary tenant but no active membership for it;
- active `student` membership without an active `Student` in the same tenant;
- active `parent` membership without a `Parent` in the same tenant.

The default command is read-only and exits non-zero when it finds drift. Output
is one JSON object containing exact user/membership IDs, counts, the narrowly
supported repair plan, and a SHA-256 confirmation token bound to that exact
candidate set.

```powershell
pwsh scripts/v1/run-api-management-remote.ps1 `
  -Command 'audit_tenant_access'
```

Use `--no-fail` only when an operator needs to capture the complete dry-run JSON
without treating findings as a shell failure. It never changes data.

## Guarded repair

Repair supports only two unambiguous cases:

1. create a `student` membership when the active primary user has an active
   same-tenant `Student`;
2. deactivate an orphan `student` membership when that user has no other active
   membership, then run canonical user reconciliation/token revocation.

Any unsupported finding, candidate drift, parent mismatch, failed post-check,
wrong confirmation token, missing backup path, or existing backup file aborts
without database changes. Do not execute repair until the user has explicitly
approved the exact tenant/user/membership IDs and expected row counts printed by
the dry run. Confirm a current restorable RDS snapshot before continuing.

Run repair on the API host against the running container so the pre-write JSON
backup can be copied to persistent host storage immediately. Substitute the
exact token printed by the dry run and a UTC timestamp:

```bash
sudo install -d -m 700 /opt/academy-backups/tenant-access
sudo docker exec academy-api python manage.py audit_tenant_access \
  --execute \
  --confirm 'REPAIR_TENANT_ACCESS:<create-count>:<deactivate-count>:<plan-sha256>' \
  --backup-file '/tmp/tenant-access-<UTC>.json'
sudo docker cp \
  academy-api:/tmp/tenant-access-<UTC>.json \
  /opt/academy-backups/tenant-access/tenant-access-<UTC>.json
sudo chmod 600 /opt/academy-backups/tenant-access/tenant-access-<UTC>.json
sha256sum /opt/academy-backups/tenant-access/tenant-access-<UTC>.json
```

Copy the backup off the instance before any container refresh to the private,
versioned, default-encrypted operations archive declared by
`operations.backupBucket` in `docs/ssot/params.yaml`. Store it below a
date-scoped `tenant-access/` prefix and verify the uploaded object's checksum or
metadata against the host SHA-256. The bucket blocks all public access, denies
non-TLS requests, retains current evidence for 365 days, and retains replaced
versions for 30 days. Retain the dry-run JSON, explicit approval, container
output (including `post-repair-verification` with zero findings), host backup
path, object key, SHA-256, and RDS snapshot identifier as the release evidence.

Finally rerun the read-only command independently. A clean result exits zero and
reports `finding_count: 0`.

```powershell
pwsh scripts/v1/run-api-management-remote.ps1 `
  -Command 'audit_tenant_access'
```

## 2026-07-14 production seal evidence

- Restorable RDS snapshot: `academy-db-enterprise-qa-pre-repair-20260714-0305`
- Guarded plan: create 1 missing student membership and deactivate 15 orphan
  student memberships.
- Encrypted, versioned backup:
  `2026-07-14/tenant-access/tenant-access-20260714T0315KST.json` in the
  `operations.backupBucket` archive; host SHA-256
  `c526234125eec38a1345bb7dab6d346666665df16988a29368da1aebf2e00d28`.
- Independent post-check: `finding_count=0` for all three access invariants.
