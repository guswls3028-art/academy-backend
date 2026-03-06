# Target Architecture — Minimal SSOT-Based

**Region:** ap-northeast-2  
**VPC:** academy-v1-vpc (vpc-0831a2484f9b114c2)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              VPC: academy-v1-vpc (172.30.0.0/16)                         │
│                                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ PUBLIC SUBNETS (172.30.0.0/24, 172.30.2.0/24)                                       │ │
│  │                                                                                      │ │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                     │ │
│  │   │ API Instance 1   │  │ Messaging       │  │ AI Worker       │                     │ │
│  │   │ (academy-v1-api)│  │ Worker          │  │                 │                     │ │
│  │   │ sg: api-sg      │  │ sg: worker-sg   │  │ sg: worker-sg   │                     │ │
│  │   └────────┬────────┘  └─────────────────┘  └─────────────────┘                     │ │
│  │            │                                                                         │ │
│  │            │  ┌───────────────────────────────────────────────────────────────┐     │ │
│  │            └──► ALB (academy-v1-api-alb)                                       │     │ │
│  │                │ Target Group: academy-v1-api-tg                               │     │ │
│  │                │ Health: /healthz                                              │     │ │
│  │                └───────────────────────────────────────────────────────────────┘     │ │
│  └─────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐ │
│  │ PRIVATE SUBNETS (172.30.1.0/24, 172.30.3.0/24)                                      │ │
│  │                                                                                      │ │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                     │ │
│  │   │ RDS             │  │ Redis           │  │ Batch CE        │                     │ │
│  │   │ academy-db      │  │ academy-v1-redis│  │ (video, ops)     │                     │ │
│  │   │ sg: rds-sg      │  │ sg: redis-sg    │  │ sg: batch-sg    │                     │ │
│  │   └─────────────────┘  └─────────────────┘  └─────────────────┘                     │ │
│  └─────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘

Security Groups (5 total):
┌──────────────────┬─────────────────────────────────────────────────────────────────────┐
│ api-sg            │ API instances: 8000, ALB health                                    │
│ worker-sg         │ Messaging + AI workers: SQS, RDS, Redis, API internal               │
│ batch-sg          │ Batch CE: RDS, Redis, S3, ECR, R2 (via VPC endpoint)               │
│ redis-sg          │ Redis: 6379 from api-sg, worker-sg, batch-sg                       │
│ rds-sg            │ RDS: 5432 from api-sg, worker-sg, batch-sg                         │
└──────────────────┴─────────────────────────────────────────────────────────────────────┘

Compute:
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ ALB → API ASG (academy-v1-api-asg)     min=1, max=2                                    │
│ Workers → messaging-worker-asg         min=1, max=3                                    │
│ Workers → ai-worker-asg                min=1, max=5                                    │
│ Batch → video-batch-queue → video-batch-ce                                             │
│ Batch → video-ops-queue → video-ops-ce                                                │
└─────────────────────────────────────────────────────────────────────────────────────────┘

Storage (DO NOT DELETE):
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ RDS: academy-db                                                                         │
│ Redis: academy-v1-redis                                                                 │
│ DynamoDB: academy-v1-video-job-lock, academy-v1-video-upload-checkpoints               │
│ SQS: academy-v1-messaging-queue, academy-v1-ai-queue                                   │
└─────────────────────────────────────────────────────────────────────────────────────────┘

Scheduler:
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ EventBridge: academy-v1-reconcile-video-jobs (rate 15m) → Batch ops                     │
│ EventBridge: academy-v1-video-scan-stuck-rate (rate 5m) → Batch ops                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Security Group Consolidation

| Current | Target | Notes |
|---------|--------|-------|
| academy-v1-sg-app | api-sg | API + Workers (same as current) |
| academy-v1-sg-batch | batch-sg | Batch CE |
| academy-v1-sg-data | (merged into rds-sg, redis-sg) | Data access rules |
| academy-rds | rds-sg | RDS |
| academy-redis-sg | redis-sg | Redis |
| academy-video-batch-sg | batch-sg | Consolidate with academy-v1-sg-batch |
| academy-api-sg | DELETE | Orphan |
| academy-worker-sg | DELETE | Orphan |
| academy-v1-vpce-sg | DELETE | Orphan |
| academy-lambda-* | Manual | Lambda VPC (if Lambda used) |

---

## Minimization Targets

| Metric | Current | Target |
|--------|---------|--------|
| Security Groups (VPC) | 13 | 5 |
| Orphan ENIs | 1 | 0 |
| Unattached EIPs | 0 | 0 |
| Legacy EventBridge rules | 4 | 0 |
| Legacy SGs | 3 | 0 |
