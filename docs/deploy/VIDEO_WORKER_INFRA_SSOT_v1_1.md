# Video Worker 인프라 SSOT v1.1 (최종 확정)

**이 문서는 프로덕션 단일 기준(Single Source of Truth)이다.**  
원테이크 스크립트(`infra_full_alignment_one_take.ps1`)는 이 SSOT를 강제(enforce)하며, 구축 + 작동 검증 + 감사 PASS까지 완료해야 성공이다. **부분 해결책 불인정. 전체 정합만 허용.**

**대상 리전:** ap-northeast-2  
**대상 VPC(기본):** vpc-0831a2484f9b114c2 (API/Batch 공용 VPC)

---

## 0. 원테이크 성공 정의

- **성공 = 구축 + 작동 검증 + 감사 PASS**
- 하나라도 실패하면 **전체 FAIL**로 종료. FAIL reason + 증거(aws cli json) 출력.
- **멱등성:** 재실행해도 CE/Queue/ASG/Rule 증식 0.
- 기존 리소스가 SSOT에 어긋나면 FixMode에서 **교체/재생성** 수행.
- **기계 판정:** 사후 사람 해석 없이 PASS/FAIL 판정 가능.

---

## 1. Video Batch (고정)

| 구분 | 항목 | 값 |
|------|------|-----|
| **CE** | Name | academy-video-batch-ce-final |
| | Type | MANAGED / EC2 |
| | instanceTypes | **c6g.large 단일** |
| | minvCpus / maxvCpus / desiredvCpus | 0 / 32 / 0 (desired 수동 조작 금지) |
| | allocationStrategy | BEST_FIT_PROGRESSIVE |
| | state / status | ENABLED / VALID |
| **Queue** | Name | academy-video-batch-queue |
| | computeEnvironmentOrder | academy-video-batch-ce-final **단일** |
| | state | ENABLED |
| **JobDef** | Name | academy-video-batch-jobdef |
| | vcpus / memory / timeout | 2 / 3072 MiB / 14400초 |
| | retryStrategy | attempts = **1** |
| | image | **immutable tag 필수. :latest 금지.** |
| | submit | name만 사용. :revision 하드코딩 금지. |

---

## 2. Ops Batch (고정)

| 구분 | 항목 | 값 |
|------|------|-----|
| **CE** | Name | academy-video-ops-ce |
| | instanceTypes | default_arm64 |
| | minvCpus / maxvCpus | 0 / 2 |
| | state / status | ENABLED / VALID |
| **Queue** | Name | academy-video-ops-queue |
| | CE | 단일 연결 |
| **JobDef** | reconcile | academy-video-ops-reconcile (timeout 900, vcpu 1, mem 2048, retry 1) |
| | scanstuck | academy-video-ops-scanstuck (timeout 900, vcpu 1, mem 2048, retry 1) |
| | netprobe | academy-video-ops-netprobe (timeout 120, vcpu 1, mem 512, retry 1) |

---

## 3. EventBridge (고정)

| 규칙 | 이름 | schedule | target |
|------|------|----------|--------|
| Reconcile | academy-reconcile-video-jobs | **rate(15 minutes)** | academy-video-ops-queue |
| Scan Stuck | academy-video-scan-stuck-rate | rate(5 minutes) | academy-video-ops-queue |

- **동시 실행 방지:** 앱 레벨 Redis lock (`video:reconcile:lock`, TTL 600s). reconcile 동시 실행 1개 보장.

---

## 4. Network (절대 조건)

- **프로덕션 표준:** Private Subnet + **NAT Gateway 존재**
- **S3 Gateway Endpoint 필수**(무료). S3 트래픽은 NAT 미경유.
- 위 조건 불충족 시 원테이크는 **즉시 FAIL** (또는 FixMode에서 네트워크 기반 재구성: NAT 생성, 라우트 테이블 정리, S3 GW Endpoint 강제).
- **Netprobe가 RUNNABLE에 머무르면 무조건 FAIL.**

---

## 5. Storage (고정)

- **Launch Template root EBS:** 100GB, gp3, encrypted=true, DeleteOnTermination=true.

---

## 6. 금지 항목

- image :latest 사용 → FAIL 또는 FixMode에서 재등록.
- JobDef submit 시 :revision 하드코딩.
- Video Queue에 CE 2개 이상 연결.
- Batch retry attempts ≠ 1.
- 원테이크 PASS 전 "PASS처럼 보이게" 조작 금지. 실패 시 실패 인정 후 다음 지시 요구.

---

## 7. 원테이크 흐름 요약

1. **Preflight** — 현재 리소스 JSON 덤프 (포렌식 스냅샷).
2. **Network baseline** — Private subnet 식별/생성, NAT GW 생성, 0.0.0.0/0 → NAT 라우팅, S3 Gateway Endpoint 생성/연결.
3. **Build 서버 정합** — 빌드 인스턴스 NAT 경로 확보, SG/route/IAM 확인, SSM RunCommand로 `sts get-caller-identity` 성공 필수.
4. **Batch Video/Ops SSOT enforce** — CE/Queue/JobDef/LaunchTemplate 강제. INVALID CE 시 Queue 분리 → CE 삭제 → 재생성 → Queue 재연결.
5. **EventBridge SSOT enforce** — 규칙 스케줄/타깃 강제. EnableSchedulers 옵션에 따라 최종 ENABLED/DISABLED.
6. **RUNNABLE 폭주 청소** — Ops Queue의 RUNNABLE/RUNNING(reconcile/scanstuck/netprobe) 원테이크 실행 전 정리. 청소 후 **Netprobe 1회 제출 및 SUCCEEDED 검증** 필수.
7. **Netprobe 제출/성공 확인** — 필수. 실패 시 전체 FAIL.
8. **Audit** — VIDEO WORKER SSOT AUDIT (PASS/FAIL + reason + evidence), FINAL RESULT: PASS/FAIL.

---

## 8. Audit 출력 형식 (기계 판정용)

```
==============================
VIDEO WORKER SSOT AUDIT
==============================

[1] VIDEO CE   ...  Result: PASS | FAIL
[2] VIDEO QUEUE  ...  Result: PASS | FAIL
[3] VIDEO JOB DEF  ...  Result: PASS | FAIL
[4] OPS CE   ...  Result: PASS | FAIL
[5] EVENTBRIDGE  ...  Result: PASS | FAIL
[6] NETPROBE  ...  Result: PASS | FAIL
[7] SCALING SANITY  ...  Result: PASS | FAIL

==============================
FINAL RESULT: PASS | FAIL
==============================
```

실패 시: **FAIL reason 1줄** + 관련 `aws ... describe` 결과 JSON 출력/저장.

---

## 9. 실행 예시 (1줄)

```powershell
.\scripts\infra\infra_full_alignment_one_take.ps1 -Region ap-northeast-2 -VpcId vpc-0831a2484f9b114c2 -EcrRepoUri "<acct>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<gitsha>" -FixMode -EnableSchedulers
```

- `<acct>`: `aws sts get-caller-identity --query Account --output text`
- `<gitsha>`: immutable tag (예: git rev-parse --short HEAD). **:latest 금지.**

---

## 관련 문서

- **원테이크 스크립트:** `scripts/infra/infra_full_alignment_one_take.ps1`
- **포렌식 수집:** `scripts/infra/infra_forensic_collect.ps1`
- **기존 SSOT (참고):** [VIDEO_WORKER_INFRA_SSOT_V1.md](VIDEO_WORKER_INFRA_SSOT_V1.md)
