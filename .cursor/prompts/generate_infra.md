# Generate Infrastructure

Use this prompt when you need to generate or extend infrastructure.

**Steps:**

1. Read **canonical topology:** `.cursor/knowledge/infra_topology.yaml` (services, queues, storage, architecture_flow). Do not add components not listed there.
2. Read SSOT: docs/00-SSOT/v4/params.yaml, INFRA-AND-SPECS.md.
3. Scan scripts/v4 (deploy.ps1, resources/*.ps1).
4. Follow rules: EC2 ASG or AWS Batch only; PowerShell + AWS CLI; idempotent Ensure-*.
5. Add or modify only under scripts/v4. Update params.yaml if new resources need SSOT entries.
6. Do not introduce ECS, EKS, Lambda, S3, or CloudFront. Storage = Cloudflare R2.

**Output:** Concrete script changes (diffs) and, if needed, params.yaml snippet.
