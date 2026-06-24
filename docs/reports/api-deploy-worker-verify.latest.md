# API Deploy + Worker Verification Report

**Status:** Superseded.
**Superseded by:** `docs/reports/production-canary.latest.md`, `docs/reports/deploy-verification-latest.md`, and release `docs/releases/v1.5.1.md`.
**Current capacity SSOT:** `docs/ssot/params.yaml`.

This file used to contain a 2026-06-11 deploy verification snapshot with the old two-instance API capacity baseline. It is intentionally kept as a compatibility pointer only so `*.latest.md` no longer presents that old value as current truth.

Current production capacity baseline:

| Component | Current SSOT |
|-----------|--------------|
| API ASG | min=1 desired=1 max=3, CPU target tracking 55% |
| Messaging worker ASG | min=0 desired=0 max=3 |
| AI worker ASG | min=0 desired=0 max=5 |
| Tools worker ASG | min=0 desired=0 max=2 |

Use `pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport` and `pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default` for current deploy verification.
