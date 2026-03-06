# AWS Infrastructure Dependency Graph

**Region:** ap-northeast-2 (Seoul)  
**VPC:** vpc-0831a2484f9b114c2 (academy-v1-vpc)  
**Generated:** 2026-03-06

---

## 1. High-Level Dependency Graph

```
                                    ┌─────────────────┐
                                    │   Internet      │
                                    └────────┬────────┘
                                             │
                                    ┌────────▼────────┐
                                    │  academy-v1-    │
                                    │  api-alb       │
                                    │  (sg: default) │
                                    └────────┬────────┘
                                             │
                                    ┌────────▼────────┐
                                    │ academy-v1-     │
                                    │ api-tg          │
                                    └────────┬────────┘
                                             │
┌────────────────────────────────────────────┼────────────────────────────────────────────┐
│                                            │                                            │
│  ┌──────────────────┐    ┌────────────────▼────────────────┐    ┌──────────────────┐  │
│  │ academy-v1-      │    │ academy-v1-api-asg                │    │ academy-v1-      │  │
│  │ messaging-worker │    │ (LT: academy-v1-api-lt)           │    │ ai-worker-asg    │  │
│  │ asg              │    │ sg: academy-v1-sg-app             │    │                  │  │
│  │ sg: sg-app       │    └────────────────┬────────────────┘    │ sg: sg-app       │  │
│  └────────┬─────────┘                      │                     └────────┬─────────┘  │
│           │                                 │                              │            │
│           │                    ┌────────────▼────────────┐                 │            │
│           │                    │ academy-v1-sg-app       │                 │            │
│           │                    │ (API, Workers, Build)   │                 │            │
│           │                    └────────────┬────────────┘                 │            │
│           │                                 │                              │            │
│           └────────────────────────────────┼──────────────────────────────┘            │
│                                            │                                            │
│  ┌────────────────────────────────────────▼────────────────────────────────────────┐  │
│  │ academy-v1-sg-data (RDS, Redis, DynamoDB access)                                   │  │
│  └────────────────────────────────────────┬────────────────────────────────────────┘  │
│                                            │                                            │
│  ┌─────────────────┐    ┌─────────────────▼─────────────────┐    ┌─────────────────┐  │
│  │ academy-db       │    │ academy-v1-redis                   │    │ DynamoDB        │  │
│  │ (sg: academy-rds)│    │ (sg: academy-redis-sg)            │    │ 2 tables        │  │
│  └─────────────────┘    └──────────────────────────────────┘    └─────────────────┘  │
│                                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────────────────┐
│  │ Batch (academy-v1-video-batch-ce, academy-v1-video-ops-ce)                             │
│  │ sg: academy-v1-sg-batch, academy-video-batch-sg                                         │
│  │ Queues: academy-v1-video-batch-queue, academy-v1-video-ops-queue                        │
│  └────────────────────────────────────────────────────────────────────────────────────────┘
│                                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────────────────┐
│  │ EventBridge: academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate           │
│  │ → Batch (ops queue)                                                                      │
│  └────────────────────────────────────────────────────────────────────────────────────────┘
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. EC2 → ENI → SG → ASG Mapping

| InstanceId | Name | SubnetId | SG | ASG |
|------------|------|----------|-----|-----|
| i-0d3aaca239fd1637e | academy-v1-api | subnet-09231ed7ecf59cfa4 | sg-088fa3315c12754d0 | academy-v1-api-asg |
| i-0c11e7127e7ea03f8 | academy-build-arm64 | subnet-09231ed7ecf59cfa4 | sg-088fa3315c12754d0 | (standalone) |
| i-0851018cae061ea8d | academy-v1-ai-worker | subnet-07a8427d3306ce910 | sg-088fa3315c12754d0 | academy-v1-ai-worker-asg |
| i-0b47a6fce4975ec91 | academy-v1-messaging-worker | subnet-07a8427d3306ce910 | sg-088fa3315c12754d0 | academy-v1-messaging-worker-asg |
| i-0fab4214533707c93 | (Batch) | subnet-049e711f41fdff71b | sg-0ba6fc12209bec7de | academy-v1-video-ops-ce-asg-* |

---

## 3. Security Group → ENI Attachment Count

| GroupId | GroupName | ENI Count | Usage |
|---------|-----------|-----------|-------|
| sg-088fa3315c12754d0 | academy-v1-sg-app | 4 | API, Workers, Build |
| sg-011ed1d9eb4a65b8f | academy-video-batch-sg | 25 | Batch CE instances |
| sg-0ba6fc12209bec7de | academy-v1-sg-batch | 2 | Batch Ops CE |
| sg-0405c1afe368b4e6b | default | 2 | ALB |
| sg-06cfb1f23372e2597 | academy-rds | 1 | RDS |
| sg-0f4069135b6215cad | academy-redis-sg | 1 | Redis |
| sg-0944a30cabd0c022e | academy-lambda-endpoint-sg | 1 | Lambda VPC |
| sg-0ff11f1b511861447 | academy-lambda-internal-sg | 1 | Lambda |
| sg-0caaa6c43e12758e6 | academy-lambda-video-sg | 1 | Lambda |
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | RDS/Redis SG ref |
| sg-0f8d581baa7bc39c9 | academy-v1-vpce-sg | **0** | **ORPHAN** |
| sg-0051cc8f79c04b058 | academy-api-sg | **0** | **ORPHAN** |
| sg-02692600fbf8e26f7 | academy-worker-sg | **0** | **ORPHAN** |

---

## 4. ASG → ALB/Target Group

| ASG | TargetGroup | Connected |
|-----|-------------|-----------|
| academy-v1-api-asg | academy-v1-api-tg | ✓ |
| academy-v1-messaging-worker-asg | - | N/A (workers) |
| academy-v1-ai-worker-asg | - | N/A (workers) |
| academy-v1-video-ops-ce-asg-* | - | Batch managed |

---

## 5. Batch CE → Queue Mapping

| Queue | Compute Environment | Status |
|-------|---------------------|--------|
| academy-v1-video-batch-queue | academy-v1-video-batch-ce | ENABLED |
| academy-v1-video-ops-queue | academy-v1-video-ops-ce | ENABLED |

---

## 6. EventBridge → Batch

| Rule | State | Target |
|------|-------|--------|
| academy-v1-reconcile-video-jobs | ENABLED | ops queue |
| academy-v1-video-scan-stuck-rate | ENABLED | ops queue |
| academy-reconcile-video-jobs | DISABLED | legacy |
| academy-video-scan-stuck-rate | DISABLED | legacy |
| academy-worker-autoscale-rate | DISABLED | legacy |
| academy-worker-queue-depth-rate | DISABLED | legacy |

---

## 7. IAM Roles (academy-*)

| Role | Used By |
|------|---------|
| academy-ec2-role | API, Workers (instance profile) |
| academy-batch-service-role | Batch service |
| academy-batch-ecs-instance-role | Batch EC2 |
| academy-batch-ecs-task-execution-role | Batch tasks |
| academy-video-batch-job-role | Video job |
| academy-v1-eventbridge-batch-video-role | EventBridge → Batch |
| academy-eventbridge-batch-video-role | legacy (duplicate) |
