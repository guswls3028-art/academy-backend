# RDS Downsize Report

**Generated:** 2026-06-29T05:20:00+09:00
**DB:** `academy-db`
**Region:** `ap-northeast-2`

## Decision

| Item | Value |
|------|-------|
| Current class | `db.t4g.medium` |
| Target class | `db.t4g.medium` |
| Apply mode | applied |
| Maintenance window | `thu:20:20-thu:20:50` UTC / Friday 05:20-05:50 KST |
| PendingModifiedValues | `{}` |
| Backup retention | 7 days |
| Latest restorable time at verification | 2026-06-28T20:08:54Z |
| Verified runtime | PostgreSQL `15.17`, Single-AZ, 20 GB, available |

## Evidence

| Signal | 7-day | 30-day | Disposition |
|--------|-------|--------|-------------|
| CPUUtilization | avg 5.18%, max 50.26% | avg 4.89%, max 50.26% | safe for 2 vCPU target |
| DatabaseConnections | avg 2.30, max 14 | avg 30.55, max 588 | current steady state safe; historical spike requires alarm guard |
| FreeableMemory | avg ~4.72 GiB, min ~4.68 GiB | avg ~4.62 GiB, min ~3.16 GiB | medium is acceptable with post-apply watch |
| SwapUsage | avg ~0.94 MiB, max ~0.94 MiB | avg ~0.92 MiB, max ~1.00 MiB | no memory pressure signal |
| CPUCreditBalance | full at 864 | full at 864 | no burst-credit pressure |
| FreeStorageSpace | min ~14.75 GiB | min ~14.75 GiB | storage not a blocker |

## Cost

| Class | On-demand price | Monthly compute at 730h |
|-------|-----------------|-------------------------|
| `db.t4g.large` | 0.203 USD/hr | 148.19 USD |
| `db.t4g.medium` | 0.102 USD/hr | 74.46 USD |
| Projected reduction | 0.101 USD/hr | 73.73 USD |

Storage, backup, Performance Insights, and tax are separate from the instance compute delta.

## Guardrails

- `academy-rds-DatabaseConnectionsHigh` remains at threshold `320`, about 80% of the expected `db.t4g.medium` connection budget.
- `scripts/v1/resources/rds.ps1` now treats `docs/ssot/params.yaml` `rds.instanceClass` as an enforceable pending-aware SSOT.
- Runtime verification on 2026-06-29 confirmed `db.t4g.medium` with no pending modifications.
- Post-schedule canary passed with `PASS=30 WARN=0 FAIL=0`.
