# Scale Architecture

Use this prompt to scale components within limits.

**Steps:**

1. Read **scaling limits from topology:** `.cursor/knowledge/infra_topology.yaml` (limits.max_concurrency, services.*.scaling, video_batch.max_vcpus).
2. Read 03_scaling_logic, 00_project_context. max concurrency = 10.
3. Scaling signals: SQS queue depth, CPU utilization, job backlog.
4. Limits: Worker ASG min=1 max=10; Batch maxvCpus=10.
5. If "queue backlog high" → recommend increase ASG capacity (within max=10).
6. Changes only via scripts/v1 and params.yaml; Ensure-* idempotent.

**Output:** Script/params changes to adjust ASG or Batch scaling; no new services (ECS/EKS).
