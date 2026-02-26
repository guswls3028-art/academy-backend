# Video Worker 인프라 SSOT v1.1 (Production Lock)

**이 문서는 프로덕션 단일 기준(Single Source of Truth)이다.**  
구축/수정/감사/장애 대응은 이 문서와 100% 동일해야 한다. 원테이크 스크립트는 이 SSOT를 강제(enforce)하고, 작동 검증까지 완료해야 성공이다.

**이 문서와 다른 설정은 모두 오류 또는 마이그레이션 대상이다.**

---

## 0. 불변 철학 (절대 변경 금지)

- **1 워커 = 1 작업**
- **Batch retry 사용 금지** (retryStrategy.attempts=1). 재시도는 애플리케이션 레벨(scan_stuck)에서만 수행.
- 동일 video 중복 작업 0
- reconcile 동시 실행 0
- CE/ASG 증식 0
- 완전 Idempotent
- **원테이크 1회 = 구축 + Netprobe + Audit PASS** — "리소스만 생성"은 성공이 아니다.

---

## 1. 리소스 네이밍 (고정)

| 구분 | 리소스 | 이름 |
|------|--------|------|
| **Video** | CE | academy-video-batch-ce-final |
| | Queue | academy-video-batch-queue |
| | JobDef | academy-video-batch-jobdef |
| **Ops** | CE | academy-video-ops-ce |
| | Queue | academy-video-ops-queue |
| | JobDef | academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe |
| **EventBridge** | Reconcile | academy-reconcile-video-jobs |
| | Scan Stuck | academy-video-scan-stuck-rate |

---

## 2. Video Compute Environment (고정)

| 항목 | 값 |
|------|-----|
| Name | academy-video-batch-ce-final |
| Type | MANAGED / EC2 |
| instanceTypes | **c6g.large 단일** |
| minvCpus | 0 |
| maxvCpus | **32 (고정)** |
| allocationStrategy | BEST_FIT_PROGRESSIVE |
| state | VALID + ENABLED |

**물리 강제:** c6g.large = 2 vCPU, JobDef = 2 vCPU → 인스턴스 1대에 Video Job 2개 배치 불가.

**금지:** xlarge/2xlarge 허용 금지, instanceTypes 배열 확장 금지, Spot 사용 금지, maxvCpus 임의 변경 금지.

---

## 3. Video Job Definition (고정)

| 항목 | 값 |
|------|-----|
| Name | academy-video-batch-jobdef |
| vcpus | 2 |
| memory | 3072 MiB |
| timeout | 14400초 (4시간) |
| **retryStrategy** | **attempts = 1** |
| log | CloudWatch Logs 필수 |

**retryStrategy=1 이유:** SIGTERM → job_fail_retry() 처리. 재시도는 Django scan_stuck_video_jobs. Batch retry는 비활성화가 일관성 유지. **Batch retry=2 사용 금지.**

**이미지 정책:** latest 태그 사용 금지. **immutable tag 필수** (예: academy-video-worker:\<git-sha\>). 원테이크는 latest 사용 시 FAIL, immutable tag 아니면 FAIL.

**submit 규칙:** jobDefinition은 이름만 사용. :revision 하드코딩 금지.

---

## 4. Video Job Queue (고정)

| 항목 | 값 |
|------|-----|
| Name | academy-video-batch-queue |
| computeEnvironmentOrder | academy-video-batch-ce-final **단일** |
| state | ENABLED |

**절대 금지:** CE 2개 이상 연결, fallback CE 생성, 임시 CE 생성 후 방치.

---

## 5. Ops 인프라 (고정)

**Ops CE**

| 항목 | 값 |
|------|-----|
| Name | academy-video-ops-ce |
| instanceTypes | default_arm64 |
| minvCpus | 0 |
| maxvCpus | 2 |
| retryStrategy | attempts=1 |
| state | VALID + ENABLED |

**Ops Queue:** academy-video-ops-queue, CE 단일 연결. **Ops 작업은 절대 Video Queue로 보내지 않는다.**

---

## 6. EventBridge (고정)

| 규칙 | 이름 | schedule | target | JobDef |
|------|------|----------|--------|--------|
| Reconcile | academy-reconcile-video-jobs | **rate(15 minutes)** | academy-video-ops-queue | academy-video-ops-reconcile |
| Scan Stuck | academy-video-scan-stuck-rate | rate(5 minutes) | academy-video-ops-queue | academy-video-ops-scanstuck |

**동시 실행 방지:** Redis Lock 필수. key: `video:reconcile:lock`, TTL: 600초. lock 실패 시 즉시 종료.

---

## 7. 네트워크 SSOT (고정)

- Private Subnet, NAT Gateway 존재, S3 Gateway Endpoint 필수. S3 트래픽은 NAT를 타지 않음.
- **원테이크 실패 조건:** NAT 없음 AND 필수 VPC Endpoint 미완비 → FAIL. Netprobe RUNNABLE 정체 → FAIL.

---

## 8. 스토리지 SSOT (고정)

- Launch Template root EBS: **100GB**, volumeType=gp3, encrypted=true, DeleteOnTermination=true.
- 디스크 부족은 프로덕션 장애로 간주.

---

## 9. INVALID CE 자동 복구 (강제 루틴)

CE 상태가 INVALID면: Queue에서 CE 분리 → CE DISABLED → CE 삭제 → 동일 이름으로 재생성 (c6g.large 단일) → Queue 재연결 → Netprobe 실행. **Update로 해결하려 하지 않는다.**

---

## 10. 성공 조건 (프로덕션 승인 기준)

다음 **모두** 만족해야 PASS:

- Video CE VALID + ENABLED
- Ops CE VALID + ENABLED
- Queue CE 1개 연결
- JobDef 2vCPU / 3072 / 14400 / retryStrategy=1
- immutable image tag
- reconcile rate(15 minutes)
- Netprobe SUCCEEDED
- 작업 종료 후 5~10분 내 desiredvCpus=0 수렴
- Audit PASS, 리소스 증식 없음

**하나라도 실패하면 그 환경은 프로덕션이 아니다.**

---

## 11. 정상 동작 기대값

- 영상 5개 업로드 시: 인스턴스 최대 5대, 각 인스턴스 Video Job 1개.
- reconcile 실제 실행 1개.
- 작업 종료 후 desiredvCpus=0 수렴. 원테이크 재실행해도 리소스 증가 없음.

---

## 12. 최종 운영 규정

**원테이크가 PASS한 상태만 프로덕션이다. PASS 못 하면 그 환경은 프로덕션이 아니다.**

---

## 확정 요약표

| 항목 | 확정 |
|------|------|
| instance | c6g.large 단일 |
| job | 2 vCPU / 3072 MB |
| retryStrategy | 1 |
| image | immutable tag only |
| reconcile | 15분 |
| scan_stuck | 5분 |
| Spot | 금지 |
| root EBS | 100GB gp3 |
| timeout | 14400 |
| desiredvCpus 수렴 | 필수 |

---

## 부록 A — SSOT Audit 출력 형식

원테이크 마지막에 아래 구조로 출력. 기계적으로 PASS/FAIL 판단.

```
==============================
VIDEO WORKER SSOT AUDIT
==============================

[1] VIDEO CE   Name: academy-video-batch-ce-final  State: VALID  InstanceTypes: c6g.large  Result: PASS/FAIL
[2] VIDEO QUEUE  Name: academy-video-batch-queue  CE Count: 1  Result: PASS/FAIL
[3] VIDEO JOB DEF  vCPUs: 2  Memory: 3072  retryStrategy: 1  Result: PASS/FAIL
[4] OPS CE   Name: academy-video-ops-ce  maxvCpus: 2  Result: PASS/FAIL
[5] EVENTBRIDGE  Reconcile 15min → Ops Queue  Result: PASS/FAIL
[6] NETPROBE  Status: SUCCEEDED  Result: PASS/FAIL
[7] SCALING SANITY  Result: PASS/FAIL

==============================
FINAL RESULT: PASS | FAIL
==============================
```

**자동 FAIL 조건:** CE state≠VALID, instanceTypes에 c6g.large 외 존재, Video Queue CE 개수≠1, JobDef vcpus≠2 또는 memory≠3072, EventBridge target이 Ops Queue 아님, Netprobe≠SUCCEEDED, reconcile rule이 15분이 아님, retryStrategy≠1 등.

---

## 부록 B — 장애 시 5분 진단 플로우

- **Job RUNNABLE 정체:** CE VALID? statusReason? subnets NAT/Endpoint? ECS Container Instance 수=0? → CE INVALID면 §9 강제 재생성. Netprobe로 실패 이유 확인.
- **인스턴스 폭주:** maxvCpus, Job vcpus=2, Queue CE 단일, reconcile 락 확인.
- **작업 끝났는데 인스턴스 안 내려감:** desiredvCpus, RUNNING/stuck job 확인 → scan_stuck, 필요 시 terminate.
- **Job FAILED 반복:** container exit code, CloudWatch logs, disk full, timeout → EBS/timeout/retry 정책 점검.

---

## 부록 C — 확장 전략 (참고)

- **수평 확장만 허용:** maxvCpus 상향. 1 인스턴스 = 1 작업 유지.
- **금지:** xlarge 전환, 다중 job per instance, Spot 혼입.
- Horizontal Scaling 상세 설계는 참고용이며, **현재 프로덕션 SSOT에는 수직 확장(xlarge 등) 포함하지 않음.**

---

## 관련 문서

- **실행 순서·스크립트:** [VIDEO_INFRA_ONE_TAKE_ORDER.md](VIDEO_INFRA_ONE_TAKE_ORDER.md)
- **아키텍처(코드·DB 흐름):** [VIDEO_WORKER_ARCHITECTURE_BATCH.md](../video/worker/VIDEO_WORKER_ARCHITECTURE_BATCH.md)
- **EventBridge 상태:** [EVENTBRIDGE_RULES_STATE_AND_FUTURE.md](EVENTBRIDGE_RULES_STATE_AND_FUTURE.md)
- **원테이크 스크립트:** `scripts/infra/video_worker_infra_one_take.ps1`
