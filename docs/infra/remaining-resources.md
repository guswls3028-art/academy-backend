# 남은 리소스 (Infrastructure Reset 후)

**Region:** ap-northeast-2  
**실행일시:** 2026-03-06

---

## 보호 대상 (유지)

### RDS

| ID | SG |
|----|-----|
| academy-db | sg-06cfb1f23372e2597 (academy-rds) |

### Redis (ElastiCache)

| Replication Group | SG |
|------------------|-----|
| academy-v1-redis | sg-0f04876abb91d1606 (academy-v1-sg-data) |
| academy-redis | sg-0f4069135b6215cad (academy-redis-sg) |

### DynamoDB

| Table |
|-------|
| academy-v1-video-job-lock |
| academy-v1-video-upload-checkpoints |

### SQS

| Queue |
|-------|
| academy-v1-ai-queue |
| academy-v1-ai-queue-dlq |
| academy-v1-messaging-queue |
| academy-v1-messaging-queue-dlq |

### VPC

- 기본 VPC 및 각 VPC별 default SG 유지

---

## 보호 대상 Security Groups

| SG | Name | 용도 |
|----|------|------|
| sg-06cfb1f23372e2597 | academy-rds | RDS |
| sg-0f04876abb91d1606 | academy-v1-sg-data | academy-v1-redis |
| sg-0f4069135b6215cad | academy-redis-sg | academy-redis |

---

## 기타 유지 리소스

| 리소스 | 비고 |
|--------|------|
| VPC Endpoint (S3 Gateway) | vpce-05e329f9317c25a6c |
| Default Security Groups | 각 VPC별 default SG |

---

## 정리 완료

- 모든 컴퓨트/컨트롤 플레인 리소스 삭제 완료
- 보호 대상(RDS, Redis, DynamoDB, SQS, VPC)만 유지
