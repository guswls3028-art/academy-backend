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
- **t4g.medium:** Not supported by ECS_AL2023 in ap-northeast-2 (Batch create-compute-environment rejects it).
- **m6g.medium:** 2 vCPUs, 4 GiB. Smallest supported ARM for ECS_AL2023. **Use this.**

---

## 6. Required Changes (Minimal)

| Layer | Change |
|-------|--------|
| SSOT | opsInstanceType: m6g.medium (keep); opsMaxvCpus: 2 (keep); EventBridge rate(1 hour) (already set) |
| Deploy | batch.ps1: add queue delete before CE delete (CE cannot be deleted while queue references it) |
| AWS | Ops CE/Queue: recreated after drift. EventBridge already rate(1 hour) — no change |

**Rationale:** m6g.medium is the smallest ECS_AL2023-compatible ARM instance. maxvCpus=2 limits to one instance. minvCpus=0 unchanged.
