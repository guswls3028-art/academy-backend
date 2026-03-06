# Ops Downsize — FACT REPORT

**Generated:** 2025-03-07  
**Purpose:** Verified current state before applying minimal ops footprint changes.

---

## 1. SSOT (params.yaml) — Intended Ops Settings

| Key | Value | Source |
|-----|-------|--------|
| opsComputeEnvironmentName | academy-v1-video-ops-ce | videoBatch.opsComputeEnvironmentName |
| opsQueueName | academy-v1-video-ops-queue | videoBatch.opsQueueName |
| opsInstanceType | m6g.medium | videoBatch.opsInstanceType |
| opsMaxvCpus | 2 | videoBatch.opsMaxvCpus |
| reconcileSchedule | rate(1 hour) | eventBridge.reconcileSchedule |
| scanStuckSchedule | rate(1 hour) | eventBridge.scanStuckSchedule |
| reconcileRuleName | academy-v1-reconcile-video-jobs | eventBridge.reconcileRuleName |
| scanStuckRuleName | academy-v1-video-scan-stuck-rate | eventBridge.scanStuckRuleName |

**Note:** params.yaml has no opsMinvCpus; template ops_compute_env.json hardcodes minvCpus=0.

---

## 2. Deploy Scripts — Ops Flow

| Script | Ops Usage |
|--------|-----------|
| batch.ps1 | New-OpsCE uses OpsCEInstanceType, OpsCEMaxvCpus from SSOT. Template: minvCpus=0, PLACEHOLDER_MAX_VCPUS, PLACEHOLDER_INSTANCE_TYPE |
| batch.ps1 Ensure-OpsCE | Drift = (instanceType ≠ SSOT) OR (maxvCpus ≠ SSOT). On drift: disable queue → disable CE → delete → create → enable queue. Requires -AllowRebuild |
| eventbridge.ps1 | Uses EventBridgeReconcileSchedule, EventBridgeScanStuckSchedule from SSOT. No hardcoded schedules in create/update paths |
| prune.ps1 | scheduleMap uses EventBridgeReconcileSchedule, EventBridgeScanStuckSchedule (or "rate(1 hour)" fallback) |
| ssot.ps1 | OpsCEInstanceType, OpsCEMaxvCpus, EventBridgeReconcileSchedule, EventBridgeScanStuckSchedule loaded from params |

---

## 3. Template — ops_compute_env.json

```json
minvCpus: 0 (hardcoded)
maxvCpus: PLACEHOLDER_MAX_VCPUS (from OpsCEMaxvCpus)
instanceTypes: ["PLACEHOLDER_INSTANCE_TYPE"] (single type from OpsCEInstanceType)
desiredvCpus: 0
allocationStrategy: BEST_FIT_PROGRESSIVE
ec2Configuration: ECS_AL2023
```

---

## 4. Actual AWS State (Verified)

| Resource | minvCpus | maxvCpus | desiredvCpus | instanceTypes | Status |
|----------|----------|----------|--------------|---------------|--------|
| academy-v1-video-ops-ce | 0 | 2 | 0 | [m6g.medium] | VALID, ENABLED |

| EventBridge Rule | ScheduleExpression | State |
|------------------|--------------------|-------|
| academy-v1-reconcile-video-jobs | rate(1 hour) | ENABLED |
| academy-v1-video-scan-stuck-rate | rate(1 hour) | ENABLED |

| Queue | State | Status |
|-------|-------|--------|
| academy-v1-video-ops-queue | ENABLED | VALID |

---

## 5. Capacity Analysis

- **m6g.medium:** 2 vCPUs, 4 GiB. maxvCpus=2 → effectively 1 instance.
- **Ops jobs:** reconcile 1 vCPU / 2048 MiB, scanstuck 1 vCPU / 2048 MiB. Both can run on one m6g.medium (or t4g.medium).
- **t4g.small:** 2 vCPUs, 2 GiB. Each job needs 2048 MiB; ECS overhead may cause OOM. **Not recommended.**
- **t4g.medium:** 2 vCPUs, 4 GiB. Smallest ARM type already used in stack (api, messaging, ai). Compatible, cost-effective.

---

## 6. Required Changes (Minimal)

| Layer | Change |
|-------|--------|
| SSOT | opsInstanceType: m6g.medium → t4g.medium; opsMaxvCpus: 2 (keep) |
| Deploy | No script changes; batch.ps1 already uses SSOT. Ensure-OpsCE drift will trigger recreate with -AllowRebuild |
| AWS | Ops CE: delete + recreate with t4g.medium, maxvCpus=2. EventBridge already rate(1 hour) — no change |

**Rationale:** t4g.medium is burstable, cheaper than m6g.medium, and matches the smallest ARM type in the stack. maxvCpus=2 limits to one instance. minvCpus=0 unchanged.
