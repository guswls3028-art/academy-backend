# 00-SSOT — 인프라 단일 진실(SSOT)

**정식은 v4 한 세트만 사용합니다.**

---

## 정식 문서 (v4)

| 문서 | 설명 |
|------|------|
| [v4/SSOT.md](v4/SSOT.md) | **진입 문서** — 리소스 이름·규칙·원칙 |
| [v4/params.yaml](v4/params.yaml) | 환경별 파라미터 (스크립트 단일 입력) |
| [v4/state-contract.md](v4/state-contract.md) | 상태 계약·Wait·삭제 순서 |
| [v4/runbook.md](v4/runbook.md) | 배포·검증·장애·롤백 절차 |
| [v4/evidence.schema.md](v4/evidence.schema.md) | Evidence 테이블 스키마 |
| [v4/V4-IMPLEMENTATION-SUMMARY.md](v4/V4-IMPLEMENTATION-SUMMARY.md) | v4 구현 요약 |
| [v4/reports/](v4/reports/) | drift.latest.md, audit.latest.md, history/ |

---

## 아카이브 (참고용, 실행/배포에 사용하지 않음)

- **v3_archive/** — v3 문서·증명·감사 (INFRA-SSOT-V3.*, IDEMPOTENCY-RULES, PRUNE-DELETE-ORDER 등)
- **legacy_reports_archive/** — 과거 리포트 (FULLSTACK-*, AUDIT-*, OPERATIONAL-* 등)

---

## 원칙·용어

- **SSOT**: Single Source of Truth. 인프라 스펙·이름·파라미터는 v4 문서와 `params.yaml`만 기준으로 한다.
- **정식 배포**: `scripts/v4/deploy.ps1` (bootstrap → deploy -Plan → 필요 시 -PruneLegacy → deploy 재실행으로 No-op 확인).
- **검증**: `scripts/v4/verify.ps1` — 새 PC 5단계 검증 자동화.
