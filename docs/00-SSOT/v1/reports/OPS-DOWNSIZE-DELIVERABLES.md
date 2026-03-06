# Ops Downsize — DELIVERABLES

**Date:** 2025-03-07  
**Status:** OPS_DOWNSIZED_AND_ALIGNED

---

## 1. FACT REPORT

See: `docs/00-SSOT/v1/reports/OPS-DOWNSIZE-FACT-REPORT.md`

---

## 2. FILES CHANGED

| File | Change |
|------|--------|
| `docs/00-SSOT/v1/params.yaml` | ops comment updated; opsInstanceType kept m6g.medium (t4g not supported by ECS_AL2023) |
| `scripts/v1/resources/batch.ps1` | Ensure-OpsCE drift flow: add queue delete before CE delete; use New-OpsQueue after CE create; fix drift message to use OpsCEInstanceType variable |
| `docs/00-SSOT/v1/reports/OPS-DOWNSIZE-FACT-REPORT.md` | Created |
| `docs/00-SSOT/v1/reports/OPS-DOWNSIZE-DELIVERABLES.md` | Created |

---

## 3. EXACT SETTINGS BEFORE → AFTER

| Setting | Before | After |
|---------|--------|-------|
| EventBridge reconcile | rate(1 hour) | rate(1 hour) (unchanged) |
| EventBridge scanstuck | rate(1 hour) | rate(1 hour) (unchanged) |
| Ops CE minvCpus | 0 | 0 (unchanged) |
| Ops CE maxvCpus | 2 | 2 (unchanged) |
| Ops CE instanceTypes | [m6g.medium] | [m6g.medium] (unchanged) |
| Ops CE desiredvCpus | 0 | 0 (unchanged) |
| Ops Queue state | ENABLED | ENABLED |
| Ops CE status | VALID | VALID |

**Note:** t4g.medium was attempted but rejected by AWS Batch (ECS_AL2023 does not support t4g in ap-northeast-2). m6g.medium is the smallest supported ARM instance. maxvCpus=2 with m6g.medium (2 vCPU) = effectively 1 instance max.

---

## 4. AWS COMMANDS USED

```powershell
# Deploy (includes Ensure-OpsCE, Ensure-OpsQueue, Ensure-EventBridgeRules)
pwsh scripts/v1/deploy.ps1 -AwsProfile default -SkipNetprobe

# Verification
aws events describe-rule --name academy-v1-reconcile-video-jobs --region ap-northeast-2 --profile default --query "ScheduleExpression"
aws events describe-rule --name academy-v1-video-scan-stuck-rate --region ap-northeast-2 --profile default --query "ScheduleExpression"
aws batch describe-compute-environments --compute-environments academy-v1-video-ops-ce --region ap-northeast-2 --profile default
aws batch describe-job-queues --job-queues academy-v1-video-ops-queue --region ap-northeast-2 --profile default
```

---

## 5. VERIFICATION RESULTS

| Check | Result |
|-------|--------|
| EventBridge reconcile rule = rate(1 hour) | ✅ |
| EventBridge scan-stuck rule = rate(1 hour) | ✅ |
| Ops CE minvCpus = 0 | ✅ |
| Ops CE max capacity bounded to one instance (maxvCpus=2, m6g.medium=2vCPU) | ✅ |
| Ops queue ENABLED | ✅ |
| Ops CE VALID | ✅ |
| Deploy scripts reflect SSOT | ✅ |
| No hardcoded 30-minute schedule in active paths | ✅ |
| No hardcoded larger ops CE settings | ✅ |

---

## 6. FINAL STATUS

**OPS_DOWNSIZED_AND_ALIGNED**

- Ops remains enabled
- Ops runs at most one m6g.medium instance when needed (maxvCpus=2)
- Ops scales to zero when idle (minvCpus=0, desiredvCpus=0)
- EventBridge triggers ops once per hour (reconcile, scanstuck)
- SSOT, deploy scripts, and AWS state are aligned
- batch.ps1 drift flow fixed: queue delete before CE delete (required by AWS Batch API)
