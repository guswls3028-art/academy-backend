# 스펙 비교: 사용자 정리 vs 문서(SSOT)·토폴로지

**기준 문서:** docs/00-SSOT/v4/params.yaml, INFRA-AND-SPECS.md, .cursor/knowledge/infra_topology.yaml  
**비교 대상:** 사용자가 정리한 1️⃣~1️⃣1️⃣ 스펙

---

## 일치하는 항목 ✅

| 구분 | 사용자 스펙 | 문서 | 비고 |
|------|-------------|------|------|
| API ASG 이름 | academy-v4-api-asg | academy-v4-api-asg | 일치 |
| API 인스턴스 | t4g.medium | t4g.medium | 일치 |
| API 역할 | Django API (Gunicorn) | 동일 | 일치 |
| Messaging 큐 | academy-v4-messaging-queue | academy-v4-messaging-queue | 일치 |
| AI 큐 | academy-v4-ai-queue | academy-v4-ai-queue | 일치 |
| Messaging/AI 인스턴스 | t4g.medium | t4g.medium | 일치 |
| Messaging/AI 스케일 | min 1 / max 10 | min 1 / max 10 | 일치 |
| Video CE 이름 | academy-v4-video-batch-ce | academy-v4-video-batch-ce | 일치 |
| Video Queue | academy-v4-video-batch-queue | academy-v4-video-batch-queue | 일치 |
| Video 인스턴스 | c6g.large | c6g.large | 일치 |
| Video max vCPU | 10 | maxvCpus: 10 | 일치 |
| RDS 이름 | academy-v4-db | academy-v4-db | 일치 |
| RDS 엔진/버전 | PostgreSQL 15 | postgres 15.16 | 일치 |
| RDS 인스턴스 | db.t4g.medium | db.t4g.medium | 일치 |
| RDS Multi AZ | ❌ (single) | multi_az 미명시(단일) | 일치 |
| Redis 이름 | academy-v4-redis | academy-v4-redis | 일치 |
| Redis 버전 | 7 | 7.1 | 일치 |
| Redis 인스턴스 | cache.t4g.small | cache.t4g.small | 일치 |
| Redis 구성 | replication group | replication: true | 일치 |
| DynamoDB Lock | academy-v4-video-job-lock | academy-v4-video-job-lock | 일치 |
| 스토리지/CDN | R2, Cloudflare CDN | 동일 | 일치 |
| 인스턴스 요약 | API/Messaging/AI t4g.medium, Video c6g.large, DB db.t4g.medium, Redis cache.t4g.small | 동일 | 일치 |

---

## 불일치·차이 ⚠️

### 1. API 스케일 (max)

| 항목 | 사용자 스펙 | 문서 (params.yaml) | 비고 |
|------|-------------|---------------------|------|
| API ASG max | **max 10** | **asgMaxSize: 2** | 문서는 API만 max=2. 사용자 스펙은 max 10. |

**결정 필요:** API ASG max를 10으로 통일할지, 2를 유지할지. (워커는 문서도 max 10.)

---

### 2. ASG·리소스 이름 (네이밍)

| 컴포넌트 | 사용자 스펙 | 문서 (params.yaml / INFRA-AND-SPECS) |
|----------|-------------|----------------------------------------|
| Messaging ASG | academy-v4-**messaging-asg** | academy-v4-**messaging-worker-asg** |
| AI ASG | academy-v4-**ai-asg** | academy-v4-**ai-worker-asg** |

**문서/스크립트 실제값:** `academy-v4-messaging-worker-asg`, `academy-v4-ai-worker-asg`  
→ 스크립트·params는 `-worker-` 포함. 사용자 스펙은 짧은 이름.

**권장:** 문서/배포 기준이므로 **문서 쪽 이름이 실제 리소스 이름**. 사용자 스펙을 `-worker-asg`로 맞추거나, “표기만 짧게”라고 구분해 두는 것이 좋음.

---

### 3. Video Job Definition 이름

| 항목 | 사용자 스펙 | 문서 (params.yaml) |
|------|-------------|---------------------|
| JobDef | academy-v4-**video-job-definition** | academy-v4-**video-batch-jobdef** |

**실제 SSOT:** `workerJobDefName: academy-v4-video-batch-jobdef`  
→ **문서가 맞음.** JobDef 이름은 `academy-v4-video-batch-jobdef`.

---

### 4. EventBridge 규칙 이름

| 사용자 스펙 | 문서 (params.yaml) | 비고 |
|-------------|---------------------|------|
| academy-v4-**batch-reconcile** | academy-v4-**reconcile-video-jobs** | 규칙 이름 다름 |
| academy-v4-**scan-stuck-jobs** | academy-v4-**video-scan-stuck-rate** | 규칙 이름 다름 |
| academy-v4-**network-probe** | (EventBridge 규칙 아님) | 네트워크 확인은 **Batch Ops JobDef** (academy-v4-video-ops-netprobe) + 배포 시 Netprobe job 실행 |

**실제:** EventBridge **규칙**은 2개만 있음.  
- `academy-v4-reconcile-video-jobs` (batch 상태 점검, 15분 rate)  
- `academy-v4-video-scan-stuck-rate` (stuck job 감지, 5분 rate)  

network-probe는 EventBridge 규칙이 아니라 **Batch Job** (academy-v4-video-ops-netprobe)으로 Ops Queue에 제출되어 실행됨.

---

### 5. infra_topology.yaml과의 차이

| 항목 | 사용자 스펙 | infra_topology.yaml |
|------|-------------|---------------------|
| API scaling.max | 10 (사용자) | 10 (topology에는 max: 10) |
| params.yaml API | max 2 | — (topology는 max 10으로 되어 있음) |

토폴로지에는 API도 `scaling.max: 10`으로 되어 있어, **params.yaml(API max=2)과 토폴로지(API max=10)가 불일치**합니다.  
→ API max를 2로 고정할지 10으로 통일할지 결정 필요.

---

## 요약 표 (문서 기준 = 진실)

| # | 리소스 | 이름 (문서 기준) | 사용자 스펙과 일치? |
|---|--------|------------------|----------------------|
| 1 | API ASG | academy-v4-api-asg | ✅ 이름 일치 / ⚠️ max 2 vs 10 |
| 2 | Messaging ASG | academy-v4-**messaging-worker-asg** | ⚠️ 사용자: messaging-asg |
| 3 | AI ASG | academy-v4-**ai-worker-asg** | ⚠️ 사용자: ai-asg |
| 4 | Video Batch CE | academy-v4-video-batch-ce | ✅ |
| 4 | Video JobDef | academy-v4-video-**batch-jobdef** | ⚠️ 사용자: video-job-definition |
| 5 | RDS | academy-v4-db | ✅ |
| 6 | Redis | academy-v4-redis | ✅ |
| 7 | DynamoDB Lock | academy-v4-video-job-lock | ✅ |
| 8 | SQS 3개 | 동일 이름 | ✅ |
| 9 | EventBridge 규칙 | academy-v4-reconcile-video-jobs, academy-v4-video-scan-stuck-rate | ⚠️ 사용자: batch-reconcile, scan-stuck-jobs; network-probe는 규칙이 아님 |
| 10 | R2 / CDN | Cloudflare R2, Cloudflare CDN | ✅ |
| 11 | 인스턴스 타입 요약 | 동일 | ✅ |

---

## 권장 조치

1. **API ASG max:** 2 vs 10 중 하나로 결정 후 params.yaml · INFRA-AND-SPECS · infra_topology.yaml 한 곳으로 통일.
2. **ASG 이름:** “실제 리소스 이름 = 문서”이므로 사용자 스펙/토폴로지에는 `academy-v4-messaging-worker-asg`, `academy-v4-ai-worker-asg` 사용 권장.
3. **Video JobDef:** 문서대로 `academy-v4-video-batch-jobdef`로 통일.
4. **EventBridge:** 규칙 이름은 문서대로 `academy-v4-reconcile-video-jobs`, `academy-v4-video-scan-stuck-rate` 사용. network-probe는 EventBridge 규칙이 아니라 Batch Ops JobDef(academy-v4-video-ops-netprobe)로 표기 권장.

이 비교 결과를 반영해 **사용자 스펙 정리본** 또는 **infra_topology.yaml**을 문서와 맞출 수 있습니다. 원하면 어떤 쪽을 문서 기준으로 수정할지(사용자 스펙 vs topology vs params) 지정해 주시면 그에 맞춰 수정 초안 제안하겠습니다.
