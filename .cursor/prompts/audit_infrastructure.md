# Audit Infrastructure

Use this prompt to audit existing infrastructure against SSOT and rules.

**Steps:**

1. Read docs/00-SSOT/v4 (params.yaml, INFRA-AND-SPECS.md, SSOT.md).
2. Scan scripts/v4 and .github/workflows.
3. Compare: actual resource names, instance types, scaling limits, deployment path.
4. Check: no ECS/EKS/Lambda (except legacy), no S3 (R2 only), ASG maxSize/Batch maxvCpus ≤ 10, t4g.medium for API/workers, c6g.large for Video batch.
5. Report drift: missing resources, wrong types, script vs SSOT mismatch.

**Output:** Audit report (list of compliant items and drift items with file/line or resource name).
