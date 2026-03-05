# Generate Infrastructure

Use this prompt when you need to generate or extend infrastructure.

**Steps:**

1. Read SSOT: docs/00-SSOT/v4/params.yaml, INFRA-AND-SPECS.md.
2. Scan scripts/v4 (deploy.ps1, resources/*.ps1).
3. Follow rules: EC2 ASG or AWS Batch only; PowerShell + AWS CLI; idempotent Ensure-*.
4. Add or modify only under scripts/v4. Update params.yaml if new resources need SSOT entries.
5. Do not introduce ECS, EKS, Lambda, S3, or CloudFront. Storage = Cloudflare R2.

**Output:** Concrete script changes (diffs) and, if needed, params.yaml snippet.
