# Academy 인프라 현황 요약 (1페이지)

**기준일:** 2026-02-27 · **Region:** ap-northeast-2 · **Account:** 809466760795

---

## Batch

| 구분 | 리소스 | 상태 | 비고 |
|------|--------|------|------|
| **CE** | academy-video-batch-ce-final | VALID / ENABLED | c6g.large, maxvCpus=32, desiredvCpus=2 |
| **CE** | academy-video-ops-ce | VALID / ENABLED | default_arm64, maxvCpus=2, desiredvCpus=1 |
| **Queue** | academy-video-batch-queue | ENABLED | CE: academy-video-batch-ce-final (order=1) |
| **Queue** | academy-video-ops-queue | ENABLED | CE: academy-video-ops-ce (order=1) |

**Job Definition (최신 리비전):** academy-video-batch-jobdef:32, academy-video-ops-reconcile:16, academy-video-ops-scanstuck:16, academy-video-ops-netprobe:16 · 이미지: `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest`

---

## EventBridge

| Rule | State | Schedule |
|------|--------|----------|
| academy-reconcile-video-jobs | ENABLED | rate(15 minutes) |
| academy-video-scan-stuck-rate | ENABLED | rate(5 minutes) |
| academy-worker-autoscale-rate | DISABLED | rate(1 minute) |
| academy-worker-queue-depth-rate | DISABLED | rate(1 minute) |

---

## ECS / Auto Scaling

| 리소스 | 용도 | Desired | Min | Max | 인스턴스 |
|--------|------|---------|-----|-----|----------|
| academy-video-batch-ce-final (ASG) | Video Batch CE | 2 | 0 | 2 | 1× c6g.large (ap-northeast-2b) |
| academy-video-ops-ce (ASG) | Ops CE | 1 | 0 | 1 | 1× m6g.medium (ap-northeast-2b) |
| academy-messaging-worker-asg | Messaging Worker | 0 | 0 | 10 | — |
| academy-ai-worker-asg | AI Worker | 0 | 0 | 10 | — |

**ECS 클러스터:** academy-video-batch-ce-final_Batch_*, academy-video-ops-ce_Batch_*

---

## IAM (Academy 관련)

- **Batch:** academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-task-execution-role, academy-video-batch-job-role  
- **EventBridge:** academy-eventbridge-batch-video-role  
- **기타:** academy-ec2-role, academy-lambda

---

## 네트워크

| EIP | 용도 | 연결 |
|-----|------|------|
| 15.165.147.157 | API | i-0c8ae616abf345fd1 (eipalloc-071ef2b5b5bec9428) |
| 54.116.9.142 | (미연결 인스턴스) | eni만 연결 |
| 54.180.207.91 | RDS 관리 | Service Managed |

---

## 정리

- Batch CE/Queue/JobDef·EventBridge 규칙·ASG·EIP·IAM이 SSOT v3 기준과 일치하며, Video/Ops 큐는 ENABLED, Ops CE는 default_arm64·ECS_AL2023·maxvCpus=2로 구성됨.  
- Job Definition 구 리비전(이미지 `<acct>` 플레이스홀더 등)은 ACTIVE 다수 존재하나, 배포 스크립트는 최신 리비전만 사용.
