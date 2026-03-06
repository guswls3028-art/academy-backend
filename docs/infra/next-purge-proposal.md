# Next Purge Wave — Compute Layer Rebuild

**Prerequisite:** Service is already down. Goal: minimize spend, prepare for clean rebuild.

---

## Phase 2: Compute Purge (Order Matters)

### 1. EventBridge (remove targets, delete rules)
```
academy-v1-reconcile-video-jobs
academy-v1-video-scan-stuck-rate
```
- Remove Batch targets first
- Delete rules

### 2. Batch
- Disable + delete job queues: academy-v1-video-batch-queue, academy-v1-video-ops-queue
- Disable + delete compute environments: academy-v1-video-batch-ce, academy-v1-video-ops-ce
- Deregister job definitions (academy-v1-video-batch-jobdef, academy-v1-video-ops-*)

### 3. API ASG
- Scale academy-v1-api-asg to 0
- Delete ASG (force)
- Wait for instances to terminate

### 4. Worker ASGs
- Scale academy-v1-messaging-worker-asg to 0
- Scale academy-v1-ai-worker-asg to 0
- Delete both ASGs (force)

### 5. Batch-managed ASG
- academy-v1-video-ops-ce-asg-* (managed by Batch, will be removed when CE is deleted)

### 6. ALB / Target Group
- Delete listener rules
- Delete listener
- Delete target group academy-v1-api-tg
- Delete load balancer academy-v1-api-alb

### 7. Launch Templates
- academy-v1-api-lt
- academy-v1-messaging-worker-lt
- academy-v1-ai-worker-lt
- Batch-managed LTs (orphaned after CE delete)

### 8. Orphan Launch Templates
- Batch-lt-08d5e4c7-e9bc-391b-b018-918e11e79a03 (not used by any ASG)
- Batch-lt-3b6e6d88-aff9-3c6c-ad4e-be33f94acaac (not used by any ASG)

---

## Preserved (Stateful)
- RDS academy-db
- Redis academy-v1-redis
- DynamoDB tables
- SQS queues
- VPC, subnets
- IAM roles
- ECR repositories
- SSM parameters

---

## Execution
```powershell
pwsh scripts/v1/deploy.ps1 -PurgeAndRecreate -SkipNetprobe -RelaxedValidation
```
Or run steps manually in the order above.
