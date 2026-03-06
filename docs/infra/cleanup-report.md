# AWS Cleanup Report

**Region:** ap-northeast-2  
**Date:** 2026-03-06  
**Service Status:** Non-functional (cleanup priority)

---

## DELETED (Wave 1)

### ENI
| Resource | Description |
|----------|-------------|
| eni-0cba52698eb339acb | AWS Lambda VPC ENI-academy-worker-queue-depth-metric (status=available, orphan) |

### Security Groups
| Resource | Name |
|----------|------|
| sg-0f8d581baa7bc39c9 | academy-v1-vpce-sg |
| sg-0051cc8f79c04b058 | academy-api-sg |
| sg-02692600fbf8e26f7 | academy-worker-sg |

### EventBridge Rules
| Rule | State (before) |
|------|----------------|
| academy-reconcile-video-jobs | DISABLED |
| academy-video-scan-stuck-rate | DISABLED |
| academy-worker-autoscale-rate | DISABLED |
| academy-worker-queue-depth-rate | DISABLED |

### SG Rule Revocations (to enable deletion)
- academy-rds: revoked sg-0051cc8f79c04b058, sg-02692600fbf8e26f7 from port 5432
- academy-redis-sg: revoked sg-0051cc8f79c04b058, sg-02692600fbf8e26f7 from port 6379
- academy-lambda-internal-sg: revoked sg-0051cc8f79c04b058, sg-02692600fbf8e26f7 from port 443
- academy-api-sg: revoked all cross-SG references before delete

---

## NOT DELETED (No Unattached Resources)

### Elastic IPs
All 3 EIPs are attached:
- eipalloc-005028ec477ae0819 → ALB (service-managed)
- eipalloc-0cf9f6d0e100d6787 → ALB (service-managed)
- eipalloc-02bcb9e54f8f9cca3 → RDS (service-managed)

---

## REMAINING (Protected / In Use)

### Protected (DO NOT DELETE)
- RDS: academy-db
- Redis: academy-v1-redis
- DynamoDB: academy-v1-video-job-lock, academy-v1-video-upload-checkpoints
- SQS: academy-v1-messaging-queue, academy-v1-ai-queue (and DLQs)

### Active Compute (Next Purge Candidates)
- EC2: 4 running (api, ai-worker, messaging-worker, Batch ops)
- ASG: 4 (api, messaging, ai, video-ops-ce)
- ALB: academy-v1-api-alb
- Target Group: academy-v1-api-tg
- Batch CE: academy-v1-video-batch-ce, academy-v1-video-ops-ce
- Batch Queues: academy-v1-video-batch-queue, academy-v1-video-ops-queue
- EventBridge: academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate (ENABLED)

---

## Next Purge Wave (Compute Rebuild)

See `next-purge-proposal.md` for Phase 2.
