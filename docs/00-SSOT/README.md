# 00-SSOT — 인프라 단일 진실(SSOT)

**정식: 풀셋팅 v1.** (기존 v4 인프라 제거 후 v1으로 새로 셋팅)

---

## 정식 문서 (v1)

| 문서 | 설명 |
|------|------|
| [v1/SSOT.md](v1/SSOT.md) | **진입 문서** — 리소스 이름·규칙·원칙 |
| [v1/params.yaml](v1/params.yaml) | 환경별 파라미터 (스크립트 단일 입력). **API ASG max=2 고정** |
| [v1/INFRA-AND-SPECS.md](v1/INFRA-AND-SPECS.md) | **인프라·스펙 한눈에 보기** — API/빌드/AI/Messaging ASG, Video Batch |

---

## 정식 배포·검증

- **배포:** `scripts/v1/deploy.ps1`
- **검증:** `scripts/v1/verify.ps1` — 새 PC 5단계 검증 자동화
- **네이밍:** 모든 리소스 `academy-v1-*` (VPC, ASG, Batch, RDS, Redis, DynamoDB, EventBridge 등)

---

## 아카이브 (참고용, 실행/배포에 사용하지 않음)

- **v4/** — 이전 SSOT v4. 풀셋팅 v1 전환 후 참고용.
- **v3_archive/** — v3 문서·증명
- **legacy_reports_archive/** — 과거 리포트

---

## 원칙·용어

- **SSOT**: Single Source of Truth. 인프라 스펙·이름·파라미터는 **v1** 문서와 `docs/00-SSOT/v1/params.yaml`만 기준으로 한다.
- **정식 배포**: `scripts/v1/deploy.ps1` (bootstrap → deploy -Plan → 필요 시 -PruneLegacy → deploy 재실행으로 No-op 확인).
- **검증**: `scripts/v1/verify.ps1`
