# Incident 2026-07-30 — API DB Connection Exhaustion

**Status:** Resolved; production configuration corrected and regression soak
passed

**Incident date:** 2026-07-30 KST

**Primary symptom:** Unrelated authenticated API requests intermittently
returned HTTP 500 while `/healthz` continued to return HTTP 200.

## Impact

- The final frontend integration E2E failed student login and QnA cleanup.
- API logs showed the same database-connect failure on tenant resolution,
  community unread counts, registration requests, token login, and community
  post deletion.
- At 04:09 KST, RDS rejected application connections with
  `remaining connection slots are reserved for non-replication superuser and
  rds_reserved connections`.
- No database write corruption, migration failure, or data loss was observed.
  The failed E2E cleanup was rerun successfully after recovery and before
  frontend promotion.
- The route-normalization candidate was not promoted during the incident, so
  the previous known-good frontend revision remained in production.

## Root Cause

Production connects directly to `academy-db`; the retired RDS Proxy is not in
the request path. One API instance runs four gevent Gunicorn workers, each
admitting up to 1,000 concurrent requests. The production environment had
`DB_CONN_MAX_AGE=5`, which allowed request-scoped database connections to stay
persistent in this gevent runtime.

Repeated production E2E traffic exposed the mismatch. CloudWatch
`DatabaseConnections` rose from 143 to 391 over 55 minutes. Read-only socket
inventory showed that the single `academy-api` container owned 390 established
connections. RDS readback after recovery confirmed `max_connections=400` and
`superuser_reserved_connections=3`.

The existing `academy-rds-DatabaseConnectionsHigh` alarm correctly entered
`ALARM` above 320. `/healthz` did not expose the failure because it is a
liveness endpoint and intentionally does not query the database.

## Resolution

- Read `/academy/api/env` without printing its SecureString value and verified
  the production settings-module boundary.
- Changed only `DB_CONN_MAX_AGE` from `5` to `0`, producing SSM parameter
  version 74.
- Applied the new environment with
  `scripts/v1/refresh-api-env.ps1`, whose container replacement keeps the old
  container as a local rollback candidate until both health probes pass.
- The refresh returned
  `API_ENV_REFRESH_PASS healthz=200 health=200`.
- Post-refresh readback confirmed `DB_CONN_MAX_AGE=0`; the API container's
  established PostgreSQL sockets fell from 390 to 0.

## Verification Evidence

- Production `/healthz`: HTTP 200.
- Production `/health`: HTTP 200 with `database=connected`.
- RDS: `available`, PostgreSQL 15, `db.t4g.medium`, no pending modification.
- API ASG: one in-service instance, matching the normal cost baseline.
- AI and Tools ASGs: desired 0; Messaging remains its one-instance warm
  baseline.
- Frontend PR E2E run
  [30482938577, attempt 2](https://github.com/guswls3028-art/academy-frontend/actions/runs/30482938577/attempts/2)
  passed 28 tests with four intentional skips, including the login and QnA
  round-trip/cleanup paths that failed during saturation.
- Immediately after the two-minute E2E load, the API container had zero
  established PostgreSQL sockets. Database activity contained only the
  diagnostic connection and one idle non-API connection.
- `academy-rds-DatabaseConnectionsHigh` returned to `OK` at
  04:21:42 KST. A final public `/health` probe still returned HTTP 200 with
  `database=connected`.

## Prevention

- Keep production API `DB_CONN_MAX_AGE=0` while using direct RDS with gevent.
- Do not enable persistent Django DB connections without an isolated
  concurrency soak and a measured connection-budget review.
- Treat `/health` and the RDS connection alarm as the database availability
  evidence; `/healthz` alone is insufficient.
- During connection exhaustion, identify the owning client before rebooting
  RDS. If the API owns the connections, use the rollback-protected env refresh
  after correcting SSM instead of restarting the database.
- Keep the 320-connection alarm threshold and watch it return to `OK` under its
  configured two-period, missing-data-not-breaching evaluation after recovery.

## Release Reference

- Backend application image remained the already promoted immutable digest;
  this was a production environment correction, not a code deployment.
- SSM `/academy/api/env`: version 74.
- Frontend route candidate passed its post-recovery PR E2E and remains governed
  by the normal production promotion gates.
