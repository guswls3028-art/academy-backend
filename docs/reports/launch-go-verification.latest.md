# Launch GO Verification

**Status:** Superseded.
**Superseded by:** `docs/releases/v1.5.1.md`, `docs/reports/production-canary.latest.md`, and `docs/reports/deploy-verification-latest.md`.
**Current capacity SSOT:** `docs/ssot/params.yaml`.

This file used to contain a 2026-06-11 launch snapshot with the old two-instance API capacity baseline. It is intentionally kept as a compatibility pointer only. Do not use it as current infrastructure truth.

Current production capacity baseline:

| Component | Current SSOT |
|-----------|--------------|
| API ASG | min=1 desired=1 max=3, CPU target tracking 55% |
| Messaging worker ASG | min=0 desired=0 max=3 |
| AI worker ASG | min=0 desired=0 max=5 |
| Tools worker ASG | min=0 desired=0 max=2 |

Current launch/deploy readiness evidence lives in the post-deploy canary and deploy-verification latest reports.
