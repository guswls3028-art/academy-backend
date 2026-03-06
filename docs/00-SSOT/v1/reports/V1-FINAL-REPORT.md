# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** [docs/00-SSOT/v1/params.yaml](../params.yaml). **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

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
- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트/R2/CDN·SQS·Video·관측
- [consistency.latest.md](./consistency.latest.md) — SSOT↔실제↔합의사항 정합성
- [front-connection.latest.md](./front-connection.latest.md) — Front V1 연결 검증·근거
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
