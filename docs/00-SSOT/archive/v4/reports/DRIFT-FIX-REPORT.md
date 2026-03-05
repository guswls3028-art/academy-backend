# v4 Drift Engine Fix — Root Cause and Changes

## 1) Root cause

- **Plan guard**: There was **no** global Plan guard in `aws.ps1` that returned `$null` for all calls. Drift and Evidence both use `Invoke-AwsJson` for describe/get/list; those were never blocked.
- **Real bug — JSON array handling in diff.ps1**:
  - PowerShell’s `ConvertFrom-Json` can deserialize a **single-element JSON array** as a **single object** (no array). So `$r.computeEnvironments` could be one PSCustomObject instead of an array.
  - In that case, `$r.computeEnvironments.Count` can be `$null` or wrong, and `$r.computeEnvironments[0]` may not be the resource object. The drift logic then treated the resource as missing.
  - Evidence used the same `Invoke-AwsJson` but checked e.g. `$ceV.computeEnvironments.Count -gt 0` and also used the first element; depending on PS version/deserialization, Evidence could still see the object and show “exists” while Drift saw “missing” due to the stricter `-not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0` and not normalizing to an array.
- **Inconsistency**: Drift only added rows for **missing** or **INVALID**; it did not add rows for **existing** resources, so the table didn’t show “exists” for current resources.

---

## 2) Files changed and why

| File | Change |
|------|--------|
| **scripts/v4/core/diff.ps1** | (1) Normalize Batch/Queue/ASG responses to arrays with `@($r.computeEnvironments)` etc. and use `$r.PSObject.Properties['computeEnvironments']` to avoid single-object unwrap. (2) Add an **“exists” row** for every SSOT resource: when resource exists and is valid, add a row with `Actual = status` (e.g. VALID, ENABLED, exists) and `Action = NoOp`. (3) EventBridge: treat `describe-rule` as a single object; add “exists” row with `Actual = $rule.State` when present. |
| **scripts/v4/core/aws.ps1** | (1) Add `Test-AwsArgsMutating` (verb heuristic: create, update, delete, put, register, deregister, attach, detach, modify, authorize, revoke, terminate, release, start, stop, add-, remove-, set-). (2) In **Plan mode**: if the command is mutating, skip execution (`Invoke-Aws` returns `$null`, `Invoke-AwsJson` returns `$null`); **read-only** (describe/get/list) run unchanged. |
| **scripts/v4/test_drift_smoke.ps1** | **New.** Calls `Get-StructuralDrift`, then runs the same describe calls (Batch CE, Batch Queue, ASG, EventBridge). Asserts: for each resource where the describe returns data, the drift row for that resource must not have `Actual = "missing"`. Fails if any “exists in AWS but DRIFT says missing”. |

---

## 3) Before / after sample output (DRIFT table)

**Before (bug):**  
Resources exist in AWS and in Evidence, but DRIFT only showed missing/recreate rows (or wrong missing due to array handling):

```
=== DRIFT ===
| ResourceType | Name | Expected | Actual | Action |
|--------------|------|----------|--------|--------|
| Batch CE | academy-video-batch-ce-final | exists | missing | Create |
| Batch CE | academy-video-ops-ce | exists | missing | Create |
| Batch Queue | academy-video-batch-queue | exists | missing | Create |
...
=== END DRIFT ===
```

**After (fix):**  
Same describe responses; arrays normalized; “exists” rows added so DRIFT matches Evidence:

```
=== DRIFT ===
| ResourceType | Name | Expected | Actual | Action |
|--------------|------|----------|--------|--------|
| Batch CE | academy-video-batch-ce-final | exists | VALID | NoOp |
| Batch CE | academy-video-ops-ce | exists | VALID | NoOp |
| Batch Queue | academy-video-batch-queue | exists | ENABLED | NoOp |
| Batch Queue | academy-video-ops-queue | exists | ENABLED | NoOp |
| EventBridge | academy-reconcile-video-jobs | exists | ENABLED | NoOp |
| EventBridge | academy-video-scan-stuck-rate | exists | ENABLED | NoOp |
| ASG | academy-messaging-worker-asg | exists | exists | NoOp |
| ASG | academy-ai-worker-asg | exists | exists | NoOp |
=== END DRIFT ===
```

If a resource is truly missing, it still shows `Actual = missing`, `Action = Create`.

---

## 4) How to verify (no AWS resource changes)

1. **Plan run**  
   `pwsh scripts/v4/deploy.ps1 -Plan`  
   - DRIFT table should show **exists/VALID/ENABLED** and **NoOp** for resources that exist in AWS (same as Evidence).

2. **Smoke script**  
   `pwsh scripts/v4/test_drift_smoke.ps1`  
   - For each SSOT resource, if the describe returns data, the corresponding drift row must not be “missing”.  
   - Exits 0 when consistent, 1 when “exists in AWS but DRIFT says missing”.

3. **PruneLegacy (dry)**  
   `pwsh scripts/v4/deploy.ps1 -Plan -PruneLegacy`  
   - Should only list delete candidates that are **not** in SSOT; critical SSOT resources (CE, queues, rules, ASGs) must not be proposed for deletion.

---

## 5) Summary

- **Root cause**: JSON single-element array deserialization in diff.ps1 plus drift only emitting rows for missing/INVALID, so existing resources could appear missing or not appear as “exists”.
- **Fixes**: Normalize describe responses to arrays in diff.ps1, add “exists”/NoOp rows for all SSOT resources, and in aws.ps1 allow read-only in Plan and block only mutating verbs.
- **Tests**: `test_drift_smoke.ps1` ensures DRIFT and Describe stay in sync; `deploy.ps1 -Plan` and `-Plan -PruneLegacy` remain dry-run only (no AWS changes).
