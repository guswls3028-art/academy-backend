# 스펙 확정 (v1 풀셋팅)

**기준:** docs/00-SSOT/v1/params.yaml  
**API ASG max:** 2 고정 (solo dev, medium reliability)  
**네이밍:** 모든 리소스 `academy-v1-*`

---

## 확정 리소스 이름 (v1)

| 구분 | 이름 |
|------|------|
| API ASG | academy-v1-api-asg (min=1, **max=2**) |
| Messaging ASG | academy-v1-messaging-worker-asg |
| AI ASG | academy-v1-ai-worker-asg |
| Video Batch CE | academy-v1-video-batch-ce |
| Video Batch Queue | academy-v1-video-batch-queue |
| Video JobDef | academy-v1-video-batch-jobdef |
| Ops CE/Queue/JobDef | academy-v1-video-ops-* |
| EventBridge | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |
| RDS | academy-v1-db |
| Redis | academy-v1-redis |
| DynamoDB Lock | academy-v1-video-job-lock |
| SQS | academy-v1-messaging-queue, academy-v1-ai-queue, academy-v1-video-batch-queue |
| VPC/SG | academy-v1-vpc, academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data |

---

## 인스턴스 타입

| 영역 | 인스턴스 |
|------|----------|
| API / Messaging / AI | t4g.medium |
| Video Batch | c6g.large |
| RDS | db.t4g.medium |
| Redis | cache.t4g.small |

이전 v4 대비 스펙 비교는 `docs/00-SSOT/v4/SPEC-COMPARISON-USER-VS-DOCS.md` 참조. v1은 위 확정값으로 통일.
