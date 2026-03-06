# Video 인프라 AWS 현황 보고서

**작성일:** 2025년 3월 7일 (한국 기준)  
**리전:** ap-northeast-2 (서울)  
**목적:** Video 관련 AWS 인프라 전체 현황 점검 및 기록

---

## 1. 요약

| 구분 | 상태 | 비고 |
|------|------|------|
| **Batch Compute Environment** | 3개 VALID, ENABLED | Standard / Long / Ops |
| **Job Queue** | 3개 ENABLED | Standard / Long / Ops |
| **Job Definition** | 5개 ACTIVE | Worker 2종 + Ops 3종 |
| **EventBridge** | 2개 ENABLED | reconcile, scanstuck (1시간 주기) |
| **DynamoDB** | 2개 ACTIVE | job-lock, upload-checkpoints |
| **EC2 (Video 전용)** | 0대 실행 중 | 모든 CE desiredvCpus=0, 스케일다운 상태 |

---

## 2. Batch Compute Environment

| CE 이름 | 상태 | 타입 | Min | Max | Desired | 인스턴스 | 용도 |
|--------|------|------|-----|-----|---------|----------|------|
| academy-v1-video-batch-ce | VALID, ENABLED | EC2 | 0 | 40 | **0** | c6g.xlarge | Standard (3시간 이하, Spot) |
| academy-v1-video-batch-long-ce | VALID, ENABLED | EC2 | 0 | 80 | **0** | c6g.xlarge | Long (3시간 초과, On-Demand) |
| academy-v1-video-ops-ce | VALID, ENABLED | EC2 | 0 | 2 | **0** | m6g.medium | Ops (reconcile, scanstuck) |

- **Standard:** BEST_FIT_PROGRESSIVE, Spot 혼합, rootVolume 200GB
- **Long:** BEST_FIT, On-Demand, rootVolume 300GB
- **Ops:** reconcile/scanstuck 1시간 주기 → 작업 없을 때 스케일다운

---

## 3. Job Queue

| 큐 이름 | 상태 | 우선순위 | 연결 CE |
|--------|------|----------|---------|
| academy-v1-video-batch-queue | ENABLED, VALID | 1 | academy-v1-video-batch-ce |
| academy-v1-video-batch-long-queue | ENABLED, VALID | 1 | academy-v1-video-batch-long-ce |
| academy-v1-video-ops-queue | ENABLED, VALID | 1 | academy-v1-video-ops-ce |

---

## 4. Job Definition

| JobDef 이름 | Revision | vCPU | Memory | Timeout | 용도 |
|-------------|----------|------|--------|---------|------|
| academy-v1-video-batch-jobdef | 20 | 2 | 4096 MiB | - | Standard 영상 인코딩 |
| academy-v1-video-batch-long-jobdef | 1 | 2 | 4096 MiB | 43200s (12h) | Long 영상 인코딩 |
| academy-v1-video-ops-reconcile | 20 | - | - | - | DB↔Batch 정합성 |
| academy-v1-video-ops-scanstuck | 20 | - | - | - | Stuck worker 감지 |
| academy-v1-video-ops-netprobe | 20 | - | - | - | 네트워크 프로브 |

**이미지:** `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest`

---

## 5. EventBridge

| 규칙 이름 | 스케줄 | 상태 | Target |
|----------|--------|------|--------|
| academy-v1-reconcile-video-jobs | rate(1 hour) | ENABLED | Ops Queue → reconcile JobDef |
| academy-v1-video-scan-stuck-rate | rate(1 hour) | ENABLED | Ops Queue → scanstuck JobDef |

- **1시간 주기:** ops CE 스케일업 빈도 감소 (이전 30분 → 1시간)

---

## 6. 큐별 Job 현황 (조회 시점)

| 큐 | RUNNABLE | RUNNING | 비고 |
|----|----------|---------|------|
| academy-v1-video-batch-queue | 0 | 0 | 유휴 |
| academy-v1-video-batch-long-queue | 0 | 0 | 유휴 |
| academy-v1-video-ops-queue | 0 | 0 | 유휴 |

- **최근 Standard 완료:** video-853dc44d, video-c4943ae4, video-c8fced6b
- **최근 Ops 완료:** scanstuck-video-jobs (정상 실행)

---

## 7. Auto Scaling Group (Batch 관련)

| ASG 이름 | Desired | Min | Max | 인스턴스 | 용도 |
|----------|---------|-----|-----|----------|------|
| academy-v1-video-batch-ce-asg-* | 0 | 0 | 0 | 0 | Standard CE |
| academy-v1-video-ops-ce-asg-* | 0 | 0 | 0 | 0 | Ops CE |

- **Long CE ASG:** 첫 job 제출 시 scale-up 되면서 생성됨 (현재 없음 = 유휴)

---

## 8. EC2 인스턴스 (실행 중)

| Instance ID | 타입 | Name | 용도 |
|-------------|------|------|------|
| i-0245b0e12cf5a925f | t4g.medium | academy-v1-api | API |
| i-086e15bf1279bd6f2 | t4g.medium | academy-v1-ai-worker | AI 워커 |
| i-0dc5242f7d8e37c76 | t4g.medium | academy-v1-messaging-worker | 메시징 워커 |

**Video Batch 전용 EC2:** 0대 (모든 CE 스케일다운)

---

## 9. DynamoDB

| 테이블 | 상태 | Item 수 | 용도 |
|--------|------|---------|------|
| academy-v1-video-job-lock | ACTIVE | 6 | 1 video 1 job 락, TTL 43200s |
| academy-v1-video-upload-checkpoints | ACTIVE | 0 | R2 multipart 업로드 체크포인트 |

---

## 10. ECR

| 리포지토리 | URI |
|------------|-----|
| academy-video-worker | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker |

---

## 11. CloudWatch Logs

| 로그 그룹 | 보존 | 용도 |
|-----------|------|------|
| /aws/batch/academy-video-ops | 30일 | reconcile, scanstuck |
| /aws/batch/academy-video-worker | 30일 | Standard/Long worker |

---

## 12. SSOT (params.yaml) vs 실제

| 항목 | SSOT | 실제 | 일치 |
|------|------|------|------|
| Standard CE maxvCpus | 40 | 40 | ✅ |
| Standard instanceType | c6g.xlarge | c6g.xlarge | ✅ |
| Long CE maxvCpus | 80 | 80 | ✅ |
| Ops CE maxvCpus | 2 | 2 | ✅ |
| Ops instanceType | m6g.medium | m6g.medium | ✅ |
| reconcileSchedule | rate(1 hour) | rate(1 hour) | ✅ |
| scanStuckSchedule | rate(1 hour) | rate(1 hour) | ✅ |

---

## 13. 라우팅 규칙

| 조건 | 큐 | JobDef |
|------|-----|--------|
| duration < 10800s (3h) | academy-v1-video-batch-queue | academy-v1-video-batch-jobdef |
| duration ≥ 10800s | academy-v1-video-batch-long-queue | academy-v1-video-batch-long-jobdef |

---

## 14. 검증 명령어

```powershell
# CE 상태
aws batch describe-compute-environments --compute-environments academy-v1-video-batch-ce academy-v1-video-batch-long-ce academy-v1-video-ops-ce --region ap-northeast-2 --profile default --query "computeEnvironments[*].{Name:computeEnvironmentName,Desired:computeResources.desiredvCpus,State:state}"

# 큐 상태
aws batch describe-job-queues --job-queues academy-v1-video-batch-queue academy-v1-video-batch-long-queue academy-v1-video-ops-queue --region ap-northeast-2 --profile default

# EventBridge
aws events list-rules --region ap-northeast-2 --profile default --query "Rules[?contains(Name,'reconcile') || contains(Name,'scan')]"
```

---

**관련 문서:** VIDEO-INFRA-STATUS-REPORT.md, VIDEO-STABILITY-FACT-REPORT.md, params.yaml (videoBatch, eventBridge)
