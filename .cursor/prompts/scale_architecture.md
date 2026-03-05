# Scale Architecture

Use this prompt to scale components within limits.

**Steps:**

1. Read 03_scaling_logic, 00_project_context. max concurrency = 10.
2. Scaling signals: SQS queue depth, CPU utilization, job backlog.
3. Limits: Worker ASG min=1 max=10; Batch maxvCpus=10.
4. If "queue backlog high" → recommend increase ASG capacity (within max=10).
5. Changes only via scripts/v4 and params.yaml; Ensure-* idempotent.

**Output:** Script/params changes to adjust ASG or Batch scaling; no new services (ECS/EKS).
