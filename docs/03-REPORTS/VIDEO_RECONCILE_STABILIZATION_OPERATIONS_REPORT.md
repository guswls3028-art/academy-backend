# Video Batch Reconcile 안정화 – 운영 검증 보고서

**문서 버전:** 1.0  
**작성일:** 2025-02-25  
**대상:** 운영팀 제출용  
**변경 범위:** Reconcile Single-flight, Ops/Video 분리, 보수적 판정, IAM/EventBridge 조정

---

## 1. 변경 개요

### 1.1 무엇을 왜 변경했는지

| 구분 | 내용 |
|------|------|
| **목적** | Reconcile job의 중첩 실행·오판으로 인한 Video READY 지연/왜곡 제거, Ops job이 Video 전용 compute를 점유하는 문제 해소 |
| **수단** | (1) Reconcile Single-flight(Redis lock), (2) Ops 전용 Queue/CE 분리, (3) DescribeJobs 실패·not_found 시 보수적 판정, (4) EventBridge 주기 완화, (5) IAM Managed Policy로 DescribeJobs 권한 보장 |

### 1.2 기존 문제 요약

- **Reconcile 중첩:** EventBridge `rate(2 minutes)`로 2분마다 실행되는데, Reconcile 실행 시간이 2분을 넘으면 새 인스턴스가 추가로 기동되어 동시에 여러 Reconcile이 RUNNING 상태가 됨.
- **DescribeJobs 권한 부재:** Reconcile job 역할(`academy-video-batch-job-role`)에 `batch:DescribeJobs` 없음 → AccessDenied 발생 시 Batch API 응답을 제대로 받지 못함.
- **오판에 의한 상태 덮어쓰기:** DescribeJobs 실패 또는 일시적 “not found”를 그대로 “job 없음”으로 해석하고 `job_fail_retry` 호출 → DB가 RUNNING인데 RETRY_WAIT로 바뀜 → Video READY 전이 지연/왜곡.
- **리소스 경쟁:** Reconcile/scan_stuck이 Video 인코딩과 동일 Queue(및 CE) 사용 → c6g 등 비싼 인스턴스를 Ops가 점유하고 scale-down 지연으로 비용 증가.

### 1.3 목표 아키텍처 요약

- **Heavy(Video 인코딩) / Light(Ops: reconcile, scan_stuck) 완전 분리**
  - Video: `academy-video-batch-queue` + 기존 CE(c6g 등) 유지.
  - Ops: `academy-video-ops-queue` + `academy-video-ops-ce` (t4g.small, max 2 vCPU, On-Demand).
- **Reconcile Single-flight:** Redis lock `video:reconcile:lock` (SETNX, TTL=600초). 락 실패 시 DB 변경 없이 즉시 종료.
- **보수적 판정:** DescribeJobs 실패 시 상태 변경 없음. not_found는 “3회 연속” 또는 “created_at 30분 초과”일 때만 fail 처리. **RUNNING → RETRY_WAIT 덮어쓰기 제거.**
- **READY 전이:** Worker `job_complete()`만 READY 전이. Reconcile은 READY를 만들지 않음(stuck detection만).
- **EventBridge:** `rate(2 minutes)` → `rate(5 minutes)`.
- **IAM:** `academy-video-batch-job-role`에 Managed Policy `AcademyAllowBatchDescribeJobs`(DescribeJobs, ListJobs) 부여. Inline policy 미사용.

---

## 2. 인프라 변경 내역

### 2.1 신규 생성 리소스

| 리소스 | 이름 | 설명 |
|--------|------|------|
| Compute Environment | `academy-video-ops-ce` | Ops 전용. instanceTypes: t4g.small, minvCpus: 0, maxvCpus: 2, On-Demand. 기존 Video CE와 동일 VPC/SG 사용. |
| Job Queue | `academy-video-ops-queue` | Ops 전용. Reconcile/scan_stuck/netprobe가 이 큐로만 제출됨. |
| IAM Managed Policy | `AcademyAllowBatchDescribeJobs` | batch:DescribeJobs, batch:ListJobs. Reconcile job role에 attach. |

- **참고:** Ops CE/Queue는 `batch_ops_setup.ps1`로 이미 존재할 수 있음. 이번 변경은 CE 스펙만 조정(t4g.small 단일, max 2 vCPU).

### 2.2 수정된 리소스

| 리소스 | 변경 내용 |
|--------|-----------|
| **Compute Environment** `academy-video-ops-ce` | 기존: t4g.micro/t4g.small, max 4 vCPU → 변경: t4g.small만, max 2 vCPU. *이미 CE가 있으면 AWS 콘솔/CLI에서 수정 불가 시 새 CE 생성 후 Queue만 재연결하는 방식 검토.* |
| **EventBridge Rule** `academy-reconcile-video-jobs` | ScheduleExpression: rate(2 minutes) → rate(5 minutes). |
| **EventBridge Rule** `academy-video-scan-stuck-rate` | ScheduleExpression: rate(2 minutes) → rate(5 minutes). |
| **EventBridge Targets** (위 두 Rule) | Target의 Job Queue ARN이 `academy-video-ops-queue`를 가리키도록 유지(기존 배포와 동일). |
| **IAM Role** `academy-video-batch-job-role` | Managed Policy `AcademyAllowBatchDescribeJobs` attach 추가. |
| **템플릿/설정 파일** | `scripts/infra/batch/ops_compute_env.json`, `scripts/infra/eventbridge/reconcile_video_jobs_schedule.json` 내용이 위 스펙과 일치하도록 수정됨. |

### 2.3 삭제된 리소스

- **없음.** 기존 Video Queue/CE/Job Definition은 그대로 두고, Ops용 리소스 추가 및 스케줄·IAM만 변경함.

---

## 3. 코드 변경 내역

### 3.1 Reconcile Single-flight 구현 방식

- **진입 시:** Redis 키 `video:reconcile:lock`에 대해 `SET key 1 NX EX 600` 수행.
  - 성공 → Reconcile 본문 실행.
  - 실패(이미 키 존재) → 로그 후 **DB/상태 변경 없이** exit 0.
- **종료 시:** `finally`에서 `DEL video:reconcile:lock` 호출로 락 해제.
- **옵션:** `--skip-lock` 시 락 없이 실행(수동 1회 실행용).

### 3.2 Redis lock 방식 (SETNX + TTL)

| 항목 | 값 |
|------|-----|
| 키 | `video:reconcile:lock` |
| TTL | 600초(10분) |
| 동작 | `set(key, "1", nx=True, ex=600)`. True면 락 획득. |
| Redis 미사용 가능 시 | 현재 구현: Redis 없으면 경고 로그 후 **락 없이 진행** (기존처럼 중첩 가능). 운영 시 Redis 가용성 전제 권장. |

- TTL만으로도 10분 후 자동 해제되므로, 프로세스 비정상 종료 시 다음 주기부터 재시도 가능.

### 3.3 not_found 보수 판정 로직

- **DB 상태가 RUNNING인 경우:** Batch에서 “not found”여도 **절대 RETRY_WAIT로 바꾸지 않음.** 해당 job은 skip.
- **그 외(QUEUED/RETRY_WAIT):** Redis 키 `video:reconcile:not_found:{job_id}`로 “연속 not_found” 횟수 카운트.
  - **fail 처리 조건:** (연속 not_found ≥ 3회) **또는** (job 생성 후 30분 이상 경과).
  - Batch에서 해당 job이 다시 조회되면(어떤 status든) 카운트 삭제(reset).

### 3.4 상태 변경 가드 로직

| Batch 상태 | Reconcile 동작 |
|------------|----------------|
| **SUCCEEDED** | **아무 상태도 변경하지 않음.** READY 전이는 worker만 수행. |
| **FAILED** | `job_fail_retry` 호출. (기존과 동일, 선택적 resubmit) |
| **RUNNING** | DB가 QUEUED일 때만 `job_set_running` 호출. |
| **not found** | 위 보수 판정(3회 연속 또는 30분 초과) 충족 시에만 `job_fail_retry`. RUNNING이면 skip. |
| **DescribeJobs 예외** | 로그 + 이벤트만 남기고 **전체 Reconcile에서 DB/상태 변경 없이** 종료. |

- Reconcile은 **READY를 만들지 않음.** SUCCEEDED 구간은 “stuck detection only”로만 두고, 실제 완료·READY 전이는 worker `job_complete()`에만 의존.

---

## 4. 안정성 검증 항목

### 4.1 Reconcile 중첩 실행 방지 검증 방법

1. **수동 검증**
   - Reconcile이 돌아가는 동안(예: `--skip-lock` 없이 실행 중) 동일 환경에서 또 다른 Reconcile 실행(또는 5분 이내에 EventBridge로 한 번 더 트리거).
   - 두 번째 인스턴스는 “Reconcile skipped - lock held” 로그 후 곧바로 종료되어야 함.
2. **배포 후 관측**
   - AWS Batch 콘솔에서 `academy-video-ops-queue` 기준으로 job name에 “reconcile”이 포함된 RUNNING job이 **동시에 1개를 초과하지 않는지** 24~48시간 확인.
3. **Redis 확인**
   - Reconcile 실행 중 Redis에서 `GET video:reconcile:lock` → "1" 존재. 실행 종료 후에는 키가 삭제되어 있음.

### 4.2 batch:DescribeJobs 권한 검증 방법

1. **배포 전**
   - Reconcile job definition이 사용하는 role이 `academy-video-batch-job-role`인지 확인.
   - `iam_attach_batch_describe_jobs.ps1` 실행 후 IAM 콘솔에서 해당 role에 `AcademyAllowBatchDescribeJobs`가 attach 되어 있는지 확인.
2. **배포 후**
   - Reconcile 로그에서 DescribeJobs 관련 AccessDenied/Throttling 로그가 없어야 함.
   - (선택) 동일 role을 가진 테스트 job에서 `aws batch describe-jobs --jobs <job-id>` 성공 여부 확인.

### 4.3 READY 전이 정상 동작 확인 방법

1. **Worker만 READY 생성**
   - 인코딩이 정상 완료된 비디오는 Worker의 `job_complete()` 호출로만 READY 전이되는지 확인.
   - Reconcile 로그에서 “RECONCILE skip SUCCEEDED” 메시지가 있고, Reconcile이 해당 job에 대해 `job_complete`를 호출하지 않는지 확인.
2. **지표**
   - “Batch SUCCEEDED but DB not READY” 같은 알람/이벤트가 기존 대비 증가하지 않아야 함. (Reconcile이 SUCCEEDED를 더 이상 건드리지 않으므로, Worker 완료 경로만 READY를 만듦.)

### 4.4 Scale-down 정상화 확인 방법

1. **리소스 분리**
   - Ops job(reconcile/scan_stuck)은 `academy-video-ops-queue`만 사용하고, Video 인코딩은 `academy-video-batch-queue`만 사용하는지 확인.
2. **Ops CE**
   - `academy-video-ops-ce`에서 minvCpus=0, maxvCpus=2이므로 Ops 부하가 없을 때 인스턴스 0대로 scale-down되는지 확인.
3. **Video CE**
   - Video 큐에 job이 없을 때 기존 대비 scale-down이 더 빨리 이루어지는지(또는 불필요한 유지가 줄었는지) 관측.

---

## 5. 비용 영향 분석

### 5.1 기존 구조 대비 EC2 유지 비용 변화

- **기존:** Reconcile/scan_stuck이 Video용 Queue(및 c6g 등 CE)에서 실행 → 인코딩 job이 없어도 Ops가 가끔 인스턴스를 점유해 scale-down 지연.
- **변경 후**
  - **Video CE:** Ops 부하가 없어지므로 “job 없을 때 유지되는 인스턴스”가 줄어들 가능성이 있음. → 비용 감소 또는 유지.
  - **Ops CE:** t4g.small, max 2 vCPU, min 0. Reconcile/scan_stuck만 사용하므로 대부분 idle → 사용 시에만 과금. Ops 전용 인스턴스 비용은 소액 증가 가능하나, Video CE에서 줄어드는 비용이 더 클 수 있음.

### 5.2 Ops/Video 분리 효과

- **계산 리소스 분리:** Video는 무거운 인코딩용(c6g 등), Ops는 가벼운 t4g.small로 분리되어 각각 독립적으로 scale in/out.
- **대기 시간 분리:** Ops job이 Video job 대기열을 밀지 않음.
- **관리 용이성:** Ops 전용 알람/지표를 Queue/CE 단위로 둘 수 있음.

---

## 6. 리스크 분석

### 6.1 Redis 장애 시 동작

| 상황 | 동작 |
|------|------|
| Redis 연결 실패 / get_redis_client() None | 현재 구현: 경고 로그 후 **락 없이 Reconcile 진행**. 이 경우 중첩 실행 가능. |
| 락 획득 중 예외 | `_acquire_reconcile_lock()`이 False 반환 → Reconcile skip. DB 변경 없음. |
| 락 해제 중 예외 | `_release_reconcile_lock()`는 로그만 하고 무시. TTL 600초 후 자동 해제. |

- **권장:** Reconcile이 기동되는 환경에서는 Redis 가용성을 보장하고, Redis 장애 시 “락 없이 진행”이 반복되지 않도록 모니터링/알람 권장.

### 6.2 Lock TTL 만료 시 동작

- TTL 600초(10분) 후 `video:reconcile:lock`이 사라짐.
- Reconcile이 10분 이상 걸리면(또는 비정상 종료로 delete 미호출) 그 전에 락이 풀림.
- 다음 EventBridge 주기(5분)에 새 Reconcile이 락을 획득할 수 있어, **최대 1개만 RUNNING**이라는 보장은 유지됨. 다만 이전 인스턴스가 아직 돌고 있으면 일시적으로 2개가 될 수 있음(10분 초과 실행 시).
- Reconcile 본문은 보수적으로 작성되어 있어, 같은 job을 두 인스턴스가 처리하더라도 중복 mark_dead 등은 idempotent에 가깝게 동작하도록 되어 있음.

### 6.3 EventBridge 실패 시 영향

- **PutRule/PutTargets 실패:** 스케줄 또는 타깃이 갱신되지 않음. 기존 rule/target이 그대로면 이전 주기(2분) 또는 이전 target으로 동작할 수 있음.
- **SubmitJob 실패:** 해당 주기의 Reconcile이 실행되지 않음. 다음 주기(5분 후)에 재시도. READY 전이는 Worker가 담당하므로 1~2주기 건너뛰어도 치명적이지 않음.
- **권장:** EventBridge 규칙의 “Invocations”/“FailedInvocations” 등 지표로 실패 건수 모니터링.

---

## 7. 롤백 전략

### 7.1 인프라 롤백 방법

| 항목 | 롤백 방법 |
|------|-----------|
| **EventBridge 주기** | `aws events put-rule --name academy-reconcile-video-jobs --schedule-expression "rate(2 minutes)" ...` (및 scan-stuck 동일) 로 2분으로 되돌림. |
| **EventBridge Target** | Reconcile/scan_stuck target을 다시 Video queue로 바꾸면 “기존처럼” Video CE에서 Ops가 돌아감. (비권장: 리소스 경쟁 재발.) |
| **IAM** | `aws iam detach-role-policy --role-name academy-video-batch-job-role --policy-arn arn:aws:iam::<account>:policy/AcademyAllowBatchDescribeJobs` 로 정책만 제거. Reconcile은 다시 DescribeJobs AccessDenied 가능. |
| **Ops CE/Queue** | Target을 다시 Video queue로 옮기면 Ops CE/Queue는 사용되지 않음. 삭제는 필요 시 별도 검토(미사용 상태로 두어도 과금 최소). |

### 7.2 코드 롤백 방법

- **Reconcile 커맨드:** 이전 버전(Redis lock 없음, not_found 즉시 fail, SUCCEEDED 시 job_complete 호출 등)으로 되돌린 뒤 재배포.
- **배포 채널:** 기존과 동일한 경로(이미지/코드 배포)로 이전 revision 배포 후, Job Definition은 필요 시 이전 revision을 가리키도록 변경.

### 7.3 안전한 복구 순서

1. **문제가 “Reconcile 로직”으로 한정될 때**  
   코드만 이전 revision으로 롤백 → 재배포. 인프라는 유지(EventBridge 5분, Ops queue, IAM policy 유지).

2. **문제가 “EventBridge/Ops queue”로 한정될 때**  
   Reconcile/scan_stuck target을 일시적으로 Video queue로 되돌려, 기존처럼 Video CE에서 Ops 실행. 이후 원인 조사 후 다시 Ops queue로 전환.

3. **전면 롤백**  
   - 코드: Reconcile 이전 버전 배포.  
   - EventBridge: rate(2 minutes), target을 Video queue로 복구.  
   - IAM: AcademyAllowBatchDescribeJobs detach(선택).  
   - Ops CE/Queue: 사용 중단(target만 변경) 또는 유지.

---

## 8. 운영 체크리스트

### 8.1 배포 후 24시간 관측 항목

- [ ] Reconcile job이 `academy-video-ops-queue`에서만 실행되는지 확인.
- [ ] RUNNING Reconcile이 동시에 1개를 초과하지 않는지 확인(최소 5~10회 주기 샘플).
- [ ] Reconcile 로그에 “DescribeJobs failed”, “AccessDenied” 없음.
- [ ] Reconcile 로그에 “skip SUCCEEDED”, “skip not_found job_id=… (DB RUNNING” 등 보수적 판정 메시지가 의도대로 나오는지 확인.
- [ ] Video 인코딩 완료 후 READY 전이가 정상적으로 이루어지는지(Worker 완료 → READY) 샘플 확인.
- [ ] Ops CE에서 idle 시 인스턴스 0으로 scale-down 되는지 확인.

### 8.2 CloudWatch 지표 확인 항목

- **Batch**
  - `academy-video-ops-queue`: Running job count (Reconcile+scan_stuck, 동시 1~2개 수준).
  - `academy-video-batch-queue`: 기존 인코딩 지표 유지, Ops로 인한 spike 감소 여부.
- **Custom(있는 경우)**
  - RECONCILE_DESCRIBE_JOBS_FAILED, BATCH_DESYNC 등 Reconcile 관련 이벤트 건수.
- **EventBridge**
  - Rule `academy-reconcile-video-jobs` Invocations / FailedInvocations.

### 8.3 RUNNING Reconcile 개수 기준

- **정상:** 동시 RUNNING Reconcile 1개(같은 주기 내 1회만 실행).
- **주의:** 일시적으로 2개(이전 인스턴스 10분 초과 실행 시). 2개가 반복되면 Reconcile 지연 또는 Redis lock 미동작 가능성 점검.
- **비정상:** 3개 이상 지속 → EventBridge 중복 트리거 또는 lock 비활용(Redis 미사용/예외) 가능성 점검.

---

## 9. 결론 및 안정성 등급

- **변경 요약:** Reconcile Single-flight(Redis lock), Ops/Video Queue·CE 분리, DescribeJobs 실패·not_found 보수적 판정, RUNNING 덮어쓰기 제거, SUCCEEDED/READY 비변경, EventBridge 5분, IAM DescribeJobs 부여를 적용함.
- **위험 완화:** DescribeJobs 실패 시 상태 변경 없음, not_found 3회·30분 규칙, RUNNING 보호로 기존 “오판에 의한 READY 지연” 요인이 제거됨.
- **잔여 리스크:** Redis 미사용 시 락 비적용(중첩 가능), Lock TTL 10분 초과 시 일시적 2개 실행 가능. 운영에서 Redis 가용성 및 Reconcile 실행 시간 모니터링 권장.

**안정성 등급: Production Ready – Stable**

- 단, **배포 후 24~48시간** 동안 위 운영 체크리스트와 RUNNING Reconcile 개수 기준을 확인하고, 이상 시 롤백 절차를 적용하는 것을 권장합니다.
