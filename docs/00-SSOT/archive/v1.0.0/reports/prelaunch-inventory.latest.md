# V1 런칭 전 인벤토리 (Prelaunch)

**전제:** 서비스 미시작(실사용/트래픽 없음). **SSOT:** docs/00-SSOT/v1/params.yaml. **리전:** ap-northeast-2.  
**수집 시각:** 2026-03-06 (스냅샷)

---

## 1) EC2 (running)

| InstanceId | Name (tag) | Type | 용도 |
|------------|------------|------|------|
| i-0afb67b956ae39197 | academy-v1-ai-worker | t4g.medium | AI worker ASG |
| i-0b44a50734e645639 | academy-v1-messaging-worker | t4g.medium | Messaging worker ASG |
| i-07f6f245de7026361 | academy-build-arm64 | t4g.medium | 빌드 서버 |
| i-06db6702b4cb48742 | academy-v1-api | t4g.medium | API ASG |
| i-0e3488fafabc46546 | academy-v1-api | t4g.medium | API ASG |
| i-05186ee56532fbc46 | academy-v1-api | t4g.medium | API ASG |
| i-08cfe5b1bac686bd1 | (없음) | m6g.medium | **Batch ops CE 소속** (ASG: academy-v1-video-ops-ce-asg-...) |

**SSOT 기준:** API desired=2. 현재 API 인스턴스 3대 → 1대 초과 가능(인스턴스 리프레시 중 또는 이전 desired 변경 잔여).

---

## 2) ASG

| ASG Name | Min | Max | Desired | Instances 수 | 비고 |
|----------|-----|-----|---------|--------------|------|
| academy-v1-ai-worker-asg | 1 | 10 | 1 | 1 | SSOT 일치 |
| academy-v1-api-asg | 2 | 4 | 2 | 3 | desired=2인데 인스턴스 3 → 정리 시 2로 수렴 |
| academy-v1-messaging-worker-asg | 1 | 10 | 1 | 1 | SSOT 일치 |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 1 | 1 | 1 | **Batch 관리.** ops CE desiredvCpus=1과 연동 |

---

## 3) ALB / Target Group

| ALB | Scheme | TG |
|-----|--------|-----|
| academy-v1-api-alb | internet-facing | academy-v1-api-tg (Port 8000, HTTP) |

---

## 4) Batch CE

| CE Name | state | status | minvCpus | maxvCpus | desiredvCpus | 비고 |
|---------|-------|--------|----------|----------|---------------|------|
| academy-v1-video-batch-ce | ENABLED | VALID | 0 | 10 | 0 | SSOT는 max=40, instanceType=c6g.xlarge → drift |
| academy-v1-video-ops-ce | ENABLED | VALID | 0 | 2 | 1 | **작업 없으면 0 수렴 목표.** 현재 desired=1 |

**academy-v1-video-batch-long-ce:** 미존재 (SSOT에 정의됨).

---

## 5) Batch Job Queue

| Queue Name | state | status |
|------------|-------|--------|
| academy-v1-video-batch-queue | ENABLED | VALID |
| academy-v1-video-ops-queue | ENABLED | VALID |

**academy-v1-video-batch-long-queue:** 미존재.

**ops queue job 수 (스냅샷 시점):** RUNNING=0, RUNNABLE=30.  
→ **30건 RUNNABLE으로 인해 Batch가 ops CE desired=1 유지.** “idle인데 노드 유지”가 아니라 **백로그 처리 중** 상태. EventBridge(15분/5분) 트리거로 reconcile·scanStuck 제출이 누적된 것으로 추정.

---

## 6) Batch Job Definitions (ACTIVE, v1 video)

- academy-v1-video-batch-jobdef
- academy-v1-video-ops-netprobe
- academy-v1-video-ops-reconcile
- academy-v1-video-ops-scanstuck

---

## 7) EventBridge Rules

| Name | State | Schedule | 비고 |
|------|-------|----------|------|
| academy-v1-reconcile-video-jobs | ENABLED | rate(15 minutes) | SSOT |
| academy-v1-video-scan-stuck-rate | ENABLED | rate(5 minutes) | SSOT |
| academy-reconcile-video-jobs | DISABLED | rate(15 minutes) | **레거시(V1 아님)** |
| academy-video-scan-stuck-rate | DISABLED | rate(5 minutes) | **레거시** |
| academy-worker-autoscale-rate | DISABLED | rate(1 minute) | 레거시 |
| academy-worker-queue-depth-rate | DISABLED | rate(1 minute) | 레거시 |

---

## 8) CloudWatch Alarms (academy 관련)

- academy-video-BatchJobFailures (OK)
- academy-video-DeadJobs (OK)
- academy-video-FailedJobs (OK)
- academy-video-QueueRunnable (OK)
- academy-video-UploadFailures (OK)

---

## 9) RDS

| DBInstanceIdentifier | Status | Class |
|---------------------|--------|-------|
| academy-db | available | db.t4g.medium |

---

## 10) Redis (ElastiCache)

| ReplicationGroupId | Status |
|--------------------|--------|
| academy-redis | available |
| academy-v1-redis | available |

**SSOT:** redis.replicationGroupId = academy-v1-redis. academy-redis는 **레거시 후보**.

---

## 11) SQS Queues

**V1 (SSOT):**
- academy-v1-ai-queue, academy-v1-ai-queue-dlq
- academy-v1-messaging-queue, academy-v1-messaging-queue-dlq

**기타(레거시 후보):**
- academy-ai-jobs-basic, academy-ai-jobs-lite, academy-ai-jobs-premium (+ dlq)
- academy-messaging-jobs (+ dlq)
- academy-video-jobs (+ dlq)

---

## 12) ops 인스턴스(i-08cfe5b1bac686bd1) 규명

- **소속:** Batch Compute Environment `academy-v1-video-ops-ce` 에 의해 생성된 ASG `academy-v1-video-ops-ce-asg-823f2525-...` 의 인스턴스.
- **현재 ops queue:** RUNNING=0, RUNNABLE=30.
- **해석:** 작업이 “없다”가 아니라 **RUNNABLE 30건 백로그**가 있어 Batch가 desiredvCpus=1(인스턴스 1대)를 유지 중. EventBridge가 15분/5분마다 reconcile·scanStuck을 제출하고, 완료보다 제출이 빠르거나 이전 잡이 누적되어 30건 대기.
- **조치 방향:** (1) RUNNABLE 30건을 소진(실행 완료) 또는 불필요 시 취소 후 (2) EventBridge 트리거가 “작업 없으면 즉시 종료”하는지 확인하고, (3) idle 시 desired=0 수렴이 되도록 모니터링. SSOT minvCpus=0, maxvCpus=2는 이미 반영됨.

---

## 13) 레거시/불필요 후보 (PHASE 4 참고)

- EventBridge: academy-reconcile-video-jobs, academy-video-scan-stuck-rate, academy-worker-autoscale-rate, academy-worker-queue-depth-rate (모두 DISABLED)
- Redis: academy-redis (v1은 academy-v1-redis)
- SQS: academy-ai-jobs-*, academy-messaging-jobs, academy-video-jobs (V1이 아닌 이름)

삭제 시 반드시 연결 관계·최근 사용 없음 증거를 남기고 단계적으로 수행.
