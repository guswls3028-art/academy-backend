# 삭제된 리소스 (Infrastructure Reset)

**Region:** ap-northeast-2  
**실행일시:** 2026-03-06

---

## 1. EC2

| 리소스 | 상태 |
|--------|------|
| 6개 인스턴스 | terminated |

---

## 2. Auto Scaling Groups

| ASG | 상태 |
|-----|------|
| academy-v1-ai-worker | deleted |
| academy-v1-api | deleted |
| academy-v1-messaging-worker | deleted |
| academy-v1-video-ops-ce | deleted |

---

## 3. Load Balancers / Target Groups / Listeners

| 리소스 | 상태 |
|--------|------|
| ALB Listeners | deleted |
| Target Groups | deleted |
| Application Load Balancers | deleted |

---

## 4. Launch Templates

| Launch Template | 상태 |
|-----------------|------|
| academy-v1-* (6개) | deleted |
| Batch-lt-* | deleted |

---

## 5. Batch

| 리소스 | 상태 |
|--------|------|
| academy-v1-video-batch-queue | deleted |
| academy-v1-video-ops-queue | deleted |
| academy-v1-video-batch-ce | deleted |
| academy-v1-video-ops-ce | deleted |
| ~100+ Job Definitions | deregistered |

---

## 6. EventBridge

| Rule | 상태 |
|------|------|
| academy-v1-reconcile-video-jobs | deleted |
| academy-v1-video-scan-stuck-rate | deleted |

---

## 7. VPC Endpoints (Interface)

| VPC Endpoint | Service | 상태 |
|--------------|---------|------|
| vpce-04fd95b8f2bd1911b | monitoring | deleted |
| vpce-0d385038c84d47b49 | monitoring | deleted |
| vpce-0dd30c5bc1d31bd81 | sqs | deleted |
| vpce-02112079b9f53f62f | ecr.api | deleted |
| vpce-048f655243f4c3ce6 | ecr.dkr | deleted |
| vpce-01909f79e1cffd102 | logs | deleted |
| vpce-0d8d3ea9b91368cae | ecs | deleted |
| vpce-079f608543b5bf812 | ecs-agent | deleted |
| vpce-0615c8e1395828539 | ecs-telemetry | deleted |
| vpce-02732201cdeb556dd | sts | deleted |

---

## 8. Security Groups

| SG | Name | 상태 |
|----|------|------|
| sg-0ba6fc12209bec7de | academy-v1-sg-batch | deleted |
| sg-051767741a1dad2a8 | academy-v4-sg-data | deleted |
| sg-088fa3315c12754d0 | academy-v1-sg-app | deleted |
| sg-0d0b80fc6c9d5d575 | academy-v4-sg-batch | deleted |
| sg-029587bac3bccb784 | academy-v4-sg-app | deleted |
| sg-0bcb33ce553123e02 | launch-wizard-5 | deleted |
| sg-0280fb93e16a9afd8 | launch-wizard-3 | deleted |
| sg-06ee0b63bfa3a00f4 | launch-wizard-2 | deleted |
| sg-011ed1d9eb4a65b8f | academy-video-batch-sg | deleted |
| sg-0944a30cabd0c022e | academy-lambda-endpoint-sg | deleted |
| sg-0ff11f1b511861447 | academy-lambda-internal-sg | deleted |
| sg-007bba3b7c40fe9c6 | academy-lambda-metric-sg | deleted |
| sg-0caaa6c43e12758e6 | academy-lambda-video-sg | deleted |

---

## 9. ENI / EIP

- **Available ENIs:** 없음 (삭제 대상 없음)
- **Unattached EIPs:** 없음
