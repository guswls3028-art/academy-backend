# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** [docs/00-SSOT/v1/params.yaml](../params.yaml). **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

---

## 우선순위: 배포 수정 전에 “돈 새는 리소스 정리” 먼저

현재 배포는 FAIL이므로, 리소스 정리를 먼저 해도 서비스 영향은 거의 없다. **정리 → 재검증** 순서로 진행한다.

### 현재 AWS 상태 (예시)
| 항목 | 현재 | 정상 V1 목표 |
|------|------|--------------|
| running instances | 4 | 3 |
| ALB | 1 | 1 |
| Security Groups | 23 | 6~8 |
| Volumes | 4 | 3~4 |
| **Elastic IP** | **4** | **0** |
| ASG | 4 | 3 (+ Batch ops 임시) |

### 정리 대상 (확정)
- **Elastic IP 4개 → 전부 삭제** (Solapi 고정 IP 요구 사라짐). Association 없으면 Release. **효과: 약 $15/월 절감.**
- **Security Group 23개 → 6~8개 수준으로 축소.** ENI에 연결되지 않은 SG(Referenced by 없음) 삭제. 유지: academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data, ALB SG, RDS SG, Redis SG.
- **API ASG 축소:** Min=2 Desired=2 Max=4 → **Min=1 Desired=1 Max=2.** **효과: 약 $30/월 절감.**
- 볼륨 4개·인스턴스 4대: 삭제 금지. Build 서버는 이미 제거된 상태.

### 실행 순서 (고정)
1. **STEP 1** Elastic IP 삭제  
2. **STEP 2** Unused Security Group 삭제  
3. **STEP 3** API ASG 줄이기 (min=1, desired=1, max=2)  
4. **STEP 4** 배포·검증 재실행: `deploy.ps1`, `run-deploy-verification.ps1`

**정리 스크립트:** [run-resource-cleanup.ps1](../../scripts/v1/run-resource-cleanup.ps1) (PHASE 1~4 수행 후 [resource-cleanup.latest.md](./resource-cleanup.latest.md) 기록).

---

## 인프라 상태 평가 (현재)

**구조 자체는 거의 정상에 가깝다.**

### 정상
| 항목 | 상태 |
|------|------|
| AI worker | 1대 |
| Messaging worker | 1대 |
| Batch / EventBridge | 정상 |
| RDS / Redis | 정상 |
| Build 서버 | 없음 (목표 달성) |

### 수정 필요
| 항목 | 현재 | 목표 |
|------|------|------|
| **API 서버** | **2대** | **1대** |

**실행 중 인스턴스 (4대):** academy-v1-api ×2, academy-v1-ai-worker ×1, academy-v1-messaging-worker ×1.  
**합의된 V1 운영:** API 1 + AI 1 + Messaging 1 = **총 3대.**

### 왜 API가 2개인가
- **academy-v1-api-asg** 현재: Min=2, Desired=2, Max=4  
- **SSOT 목표:** Min=1, Desired=1, Max=2  
- ASG가 2대 유지 중이므로, **API 1대로 줄이면 끝.**

### 해결 방법 (정석 — 콘솔 직접 조작 금지)
1. **params.yaml** 에 이미 반영되어 있으면 생략. 없으면:
   ```yaml
   api:
     asgMinSize: 1
     asgDesiredCapacity: 1
     asgMaxSize: 2
   ```
2. **배포 실행:** `pwsh scripts/v1/deploy.ps1 -Env prod`  
   → instance refresh 로 API 1대만 유지된다.

### 절대 하지 말 것
- **API 인스턴스를 콘솔/CLI로 직접 terminate 하지 말 것.**  
  ASG가 즉시 새 인스턴스를 띄운다. 반드시 **SSOT + deploy.ps1** 로 ASG를 1/1/2 로 바꾼 뒤 instance refresh 하라.

### 정리 후 목표 상태·비용
| 항목 | 목표 |
|------|------|
| EC2 running | 3 (api 1, ai 1, messaging 1) |
| ALB | 1 |
| ASG | 3 (+ Batch ops 임시) |
| Security Groups | ≈ 6 |
| Elastic IP | 0 |
| Volumes | 3~4 |

**비용:** 현재 4대 ≈ $120/월 → 정리 후 3대 ≈ $90/월.

### 다음 단계 (순서 고정)
1. **EIP 삭제** (4개 → 0)
2. **SG 정리** (Referenced by 없음 → 삭제, 23 → 6~8)
3. **API ASG 1대로 축소** (SSOT 반영 후 `deploy.ps1` 실행)
4. **deploy-verification 재실행** (`run-deploy-verification.ps1`)
5. **API /health·TG 문제 해결** (아래 “진짜 문제” 참고)

---

## 🔥 진짜 문제 (배포 FAIL의 실제 원인)

**리소스 개수가 아니라 아래 두 가지가 해결되지 않으면 배포는 계속 FAIL이다.**

| 항목 | 현재 | 필요 |
|------|------|------|
| **API /health** | **unreachable** | **200 응답** |
| **TG target health** | **0/2 healthy** | **healthy ≥ 1** |

이것을 해결하지 않으면 drift·ASG 숫자만 맞춰도 검증 스크립트는 NO-GO를 유지한다. 원인 후보: ALB→EC2 헬스체크 실패(경로/포트/SG), API 앱 미기동, Launch Template(SG) 오설정 등. [deploy-verification-latest.md](./deploy-verification-latest.md), [rca.latest.md](./rca.latest.md) 참고.

---

## 현재 상태: V1는 아직 최종 완료가 아니다

최종 검증·제출 조건을 충족하지 못한 상태이며, 아래 실패 원인 해결 후 PHASE 1→2→3→4 순서로 진행해야 한다.

---

## 실패 원인

| # | 구분 | 내용 |
|---|------|------|
| 1 | API ASG SSOT 불일치 | **기대:** Min=1, Max=2, Desired=1 → **실제:** Min=2, Max=4, Desired=2 |
| 2 | AI ASG max SSOT 불일치 | **기대:** Max=5 → **실제:** Max=10 |
| 3 | API LT drift | Launch Template가 SSOT와 불일치 (drift 존재) |
| 4 | API/TG 상태 | API /health unreachable, TG healthy 0/2 |
| 5 | Front 연결 검증 불가 | `front.domains.app`, `front.domains.api`, `front.cors.allowedOrigins` 미설정으로 프론트 연결 검증 불가 |

---

## 해야 할 일 (순서 고정)

### PHASE 1
- API **/health 200** 및 **TG healthy ≥ 1** 복구
- **API LT drift** 제거
- `run-deploy-verification.ps1` 재실행

### PHASE 2
- **SSOT 실제 반영**
  - API ASG: min=1, desired=1, max=2
  - AI ASG: min=1, desired=1, max=5
- [drift.latest.md](./drift.latest.md)에서 위 3개(API ASG, AI ASG, API LT) **Action=NoOp** 되도록 수정 후 재배포

### PHASE 3
- SSOT에 **front.domains.app**, **front.domains.api**, **front.cors.allowedOrigins** 채우기
- 프론트→API 연결 검증 수행: app 200, API /health, cache-control, CORS
- [front-connection.latest.md](./front-connection.latest.md)를 **PASS 기준**으로 갱신

### PHASE 4
- 최종 보고서 재작성
  - [consistency.latest.md](./consistency.latest.md)
  - [deploy-verification-latest.md](./deploy-verification-latest.md)
  - [V1-FINAL-REPORT.md](./V1-FINAL-REPORT.md) (본 문서)
- **최종 상태가 FAIL/NO-GO가 아닌 상태**여야 제출 가능

---

## 제출 조건 (모두 충족 시에만 제출)

| 항목 | 조건 |
|------|------|
| /health | 200 |
| TG | healthy ≥ 1 |
| API ASG | min=1, desired=1, max=2 |
| AI ASG | min=1, desired=1, max=5 |
| API LT | drift 없음 (NoOp) |
| Front connection | PASS 근거 포함 ([front-connection.latest.md](./front-connection.latest.md)) |

---

## 현재 보고서 요약 (갱신 전 스냅샷)

| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-03-06T15:05:07+09:00 |
| 최종 상태 | **FAIL** |
| GO/NO-GO | **NO-GO** |

## 상세 보고서
- [resource-cleanup.latest.md](./resource-cleanup.latest.md) — 리소스 정리 후 재검증 결과 (instances/SG/EIP/ASG)
- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트/R2/CDN·SQS·Video·관측
- [consistency.latest.md](./consistency.latest.md) — SSOT↔실제↔합의사항 정합성
- [front-connection.latest.md](./front-connection.latest.md) — Front V1 연결 검증·근거
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
