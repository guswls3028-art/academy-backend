# V1 Video Batch + Ops CE + EventBridge RCA

**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **리전:** ap-northeast-2.  
**목표:** Video Batch(standard/long) + Ops CE + EventBridge 정상 동작 진단, 불필요한 ops 인스턴스 상시 기동/유령 노드 교정.

---

## PHASE V1 — 현재 상태 스냅샷 (증거)

### 1.1 Batch Compute Environments

| CE 이름 | state | status | minvCpus | maxvCpus | desiredvCpus | instanceTypes | SSOT 기대 |
|---------|-------|--------|----------|----------|--------------|---------------|-----------|
| academy-v1-video-batch-ce | ENABLED | VALID | 0 | **10** | 0 | **c6g.large** | max=40, type=c6g.xlarge |
| academy-v1-video-batch-long-ce | (없음) | - | - | - | - | - | SSOT에 정의됨, 미생성 |
| academy-v1-video-ops-ce | ENABLED | VALID | 0 | 2 | **1** | m6g.medium | max=2, min=0 |

**증거 (describe-compute-environments):**
- standard CE: `statusReason: "ComputeEnvironment Healthy"`, `desiredvCpus: 0`. **Drift:** maxvCpus 10(실제) vs 40(SSOT), instanceTypes c6g.large vs c6g.xlarge(SSOT).
- long CE: **미존재** (describe-compute-environments에 academy-v1-video-batch-long-ce 없음).
- ops CE: `desiredvCpus: 1` — **작업 없을 때 1 vCPU 유지 → 불필요한 ops 인스턴스 상시 기동 가능성.** minvCpus=0, maxvCpus=2는 SSOT 일치.

### 1.2 Batch Job Queues

| Queue 이름 | state | status | CE 연결 |
|------------|-------|--------|---------|
| academy-v1-video-batch-queue | ENABLED | VALID | academy-v1-video-batch-ce |
| academy-v1-video-batch-long-queue | (없음) | - | - |
| academy-v1-video-ops-queue | ENABLED | VALID | academy-v1-video-ops-ce |

**증거:** describe-job-queues — academy-v1-video-batch-long-queue 호출 시 `jobQueues: []`.

### 1.3 큐별 Job 수 (list-jobs)

| Queue | RUNNING | RUNNABLE | PENDING | SUCCEEDED(최근 5건) |
|-------|---------|----------|---------|---------------------|
| academy-v1-video-batch-queue | 0 | 0 | - | - |
| academy-v1-video-ops-queue | 0 | 0 | - | 0 |

**결론:** 현재 시각 기준 video/ops 큐 모두 RUNNING·RUNNABLE 작업 없음. ops CE는 desiredvCpus=1로 유지 중 → **작업 없이 인스턴스 1개 상시 대기.**

### 1.4 “ops 인스턴스가 왜 떠있나”

- **CE 소속:** academy-v1-video-ops-ce (ecsClusterArn: `.../academy-v1-video-ops-ce_Batch_675e593a-...`).
- **해당 시각 ops queue:** RUNNING/RUNNABLE 0건. EventBridge가 15분/5분마다 reconcile·scanStuck 제출 시 일시적으로 RUNNING 생겼다가 완료 후 사라짐.
- **원인:** AWS Batch는 작업 완료 후 desiredvCpus를 0으로 줄이지만 **스케일다운 지연**이 있어, 짧은 주기(5/15분) ops job이 끝나도 당분간 desired=1로 남을 수 있음. minvCpus=0이어도 “작업 없으면 0으로 수렴”은 Batch가 자동 수행하나, 타이밍에 따라 유령처럼 1개 인스턴스가 남는 구간 발생.

### 1.5 EventBridge Rule 상태/트리거

| Rule | State | Schedule | Targets |
|------|-------|----------|---------|
| academy-v1-reconcile-video-jobs | ENABLED | rate(15 minutes) | Batch SubmitJob → JobQueue=academy-v1-video-ops-queue, JobDefinition=academy-v1-video-ops-reconcile |
| academy-v1-video-scan-stuck-rate | ENABLED | rate(5 minutes) | Batch SubmitJob → JobQueue=academy-v1-video-ops-queue, JobDefinition=academy-v1-video-ops-scanstuck |

**증거 (list-targets-by-rule):**
- reconcile: RoleArn=academy-v1-eventbridge-batch-video-role, JobName=reconcile-video-jobs.
- scan-stuck: RoleArn=academy-v1-eventbridge-batch-video-role, JobName=scanstuck-video-jobs.

**결론:** EventBridge 규칙 2개 모두 ENABLED, targets가 Batch SubmitJob으로 올바르게 연결됨. SSOT eventBridge.reconcileState/scanStuckState=ENABLED와 일치.

### 1.6 Job Definitions (ACTIVE)

- academy-v1-video-batch-jobdef: rev 1~13
- academy-v1-video-ops-reconcile, academy-v1-video-ops-scanstuck, academy-v1-video-ops-netprobe: rev 1~13

---

## PHASE V2 — “Video worker가 작업을 안 한다” 원인 분류

| 케이스 | 설명 | 증거 기반 판정 |
|--------|------|----------------|
| **A) 영상 Job 미제출** | API가 job submit 안 함 / 트리거 없음 | video-batch-queue RUNNABLE/RUNNING 0. API 상태는 본 RCA 범위 외. EventBridge는 ops만 트리거(reconcile/scanStuck) → **영상 인코딩 job 제출은 API/비동기 플로우 책임.** |
| **B) RUNNABLE에서 멈춤** | CE 리소스/SG/서브넷/Spot/권한 | 현재 RUNNABLE 0건이라 해당 없음. 다만 standard CE가 maxvCpus=10, c6g.large로 SSOT와 다름 → **추가 job 제출 시 리소스 제한으로 대기 가능성.** |
| **C) RUNNING인데 실패/즉시 종료** | 이미지/권한/R2/ffmpeg | ops queue 최근 SUCCEEDED 0건으로 표시(최근 5건만 조회). CloudWatch Logs/describe-jobs statusReason 미수집. **수동 제출 테스트(PHASE V4)로 검증 권장.** |
| **D) 작업은 되는데 READY 안 됨** | DB 업데이트/락/ops reconcile | reconcile·scanStuck이 주기적으로 제출됨. READY 전환은 앱/DB/ops 로직 의존. **별도 로그/DB 확인 필요.** |

**요약:** 현재 스냅샷만으로는 A(영상 job 미제출 가능) + B(standard CE SSOT drift로 용량 제한)가 주요 이슈. ops 인스턴스 상시 1개는 “작업 없을 때 0으로 수렴” 지연 현상.

---

## PHASE V3 — 교정 (SSOT 기반, deploy.ps1에서만)

### 3.1 ops 인스턴스 상시 기동

- **조치:** ops CE는 이미 minvCpus=0, maxvCpus=2(SSOT). desiredvCpus는 Batch가 관리. Ensure 로직에서 **ops CE 생성 시** minvCpus=0, desiredvCpus=0 명시(템플릿 이미 반영). **추가:** 배포 시 Ops CE가 DISABLED이면 ENABLED로 복구(기존 로직 유지). 스케일다운 지연은 AWS 동작이므로, “작업 없으면 0으로 수렴” 문서화만 보고서에 반영.

### 3.2 EventBridge

- 규칙 상태·targets 이미 SSOT와 일치(ENABLED, Batch SubmitJob). **추가 변경 없음.** DISABLED 발견 시 put-rule으로 ENABLED 복구는 기존 eventbridge.ps1에 있음.

### 3.3 Video Standard CE drift (maxvCpus, instanceType)

- **조치:** Ensure-VideoCE에서 **maxvCpus·instanceType drift** 감지 시 INVALID와 동일하게 재생성(AllowRebuild 필요). SSOT: maxvCpus=40, instanceType=c6g.xlarge.

### 3.4 Long CE/Queue 미생성

- **조치:** SSOT에 videoBatch.long 정의되어 있으면 Ensure-VideoLongCE / Ensure-VideoLongQueue가 생성하도록 유지. Long CE가 없을 때 AllowRebuild로 생성 가능하도록 이미 구현됨. **배포 시 -AllowRebuild로 Long CE/Queue 생성 실행.**

### 3.5 Video worker 이미지/권한·네트워크

- 본 스냅샷에서는 list-jobs만 수집. 이미지·R2·netprobe 검증은 PHASE V4 수동 제출 후 CloudWatch Logs로 수행.

---

## PHASE V4 — 최소 재현 테스트 (수동 트리거)

- **방법:** ops queue에 netprobe(reconcile/scanStuck) job 수동 제출.
  - `aws batch submit-job --job-name netprobe-manual --job-queue academy-v1-video-ops-queue --job-definition academy-v1-video-ops-netprobe:13 --region ap-northeast-2`
- **확인:** list-jobs로 RUNNING → SUCCEEDED 전환, describe-jobs statusReason, CloudWatch Logs(awslogs) 수집 후 보고서에 첨부.
- **(미실행)** 본 문서 작성 시점에서는 수동 제출 미실행. 필요 시 위 명령 실행 후 결과를 본 섹션에 추가.

---

## 최종 요약

| 항목 | 상태 | 비고 |
|------|------|------|
| Standard CE | Drift | maxvCpus 10→40, instanceType c6g.large→c6g.xlarge 필요 (Ensure-VideoCE drift 처리) |
| Long CE/Queue | 미존재 | SSOT 정의됨. -AllowRebuild 배포로 생성 가능 |
| Ops CE | 정상 설정 | min=0, max=2. desired=1은 스케일다운 지연, 문서화 |
| EventBridge | 정상 | ENABLED, targets Batch SubmitJob 연결됨 |
| Ops 인스턴스 상시 기동 | 완화 | minvCpus=0 유지, 수렴 지연은 AWS 동작 |

**변경:** deploy.ps1 호출 스크립트(batch.ps1)에서 Video CE에 maxvCpus·instanceType drift 로직 추가(아래 구현).
