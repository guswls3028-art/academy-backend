# SSOT v4 가시성 정리 + 새 PC 5단계 검증 — 작업 보고

## 1) 이동된 파일 목록 (Move-Item; 일부 미추적 파일 포함)

| 원본 | 대상 |
|------|------|
| docs/00-SSOT/SSOT.md | docs/00-SSOT/v4/SSOT.md |
| docs/00-SSOT/params.yaml | docs/00-SSOT/v4/params.yaml |
| docs/00-SSOT/state-contract.md | docs/00-SSOT/v4/state-contract.md |
| docs/00-SSOT/RUNBOOK.md | docs/00-SSOT/v4/runbook.md |
| docs/00-SSOT/evidence.schema.md | docs/00-SSOT/v4/evidence.schema.md |
| docs/00-SSOT/V4-IMPLEMENTATION-SUMMARY.md | docs/00-SSOT/v4/V4-IMPLEMENTATION-SUMMARY.md |
| docs/00-SSOT/reports/drift.latest.md | docs/00-SSOT/v4/reports/drift.latest.md |
| docs/00-SSOT/reports/audit.latest.md | docs/00-SSOT/v4/reports/audit.latest.md |
| docs/00-SSOT/reports/history/.gitkeep | docs/00-SSOT/v4/reports/history/.gitkeep |
| docs/00-SSOT/INFRA-SSOT-V3.md | docs/00-SSOT/v3_archive/INFRA-SSOT-V3.md |
| docs/00-SSOT/INFRA-SSOT-V3.params.yaml | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/INFRA-SSOT-V3.state-contract.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/IDEMPOTENCY-RULES.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/PRUNE-DELETE-ORDER-AND-RISKS.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/RESOURCE-INVENTORY.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/ONE-TAKE-DEPLOYMENT.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/SSOT-V3-CONFIRMATION-GATHER.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/CHANGELOG.md | docs/00-SSOT/v3_archive/ |
| docs/00-SSOT/AUDIT-FULL-REBUILD-SSOT-V3.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/DEPLOY-FULLSTACK-DESIGN.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/FULL-REBUILD-PROOF.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/FULL-REBUILD-STABILITY-CHANGES.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/FULLSTACK-CURRENT-STATE-REPORT.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/FULLSTACK-DRIFT-TABLE.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/FULL-STRUCTURE-DIAGNOSTIC-REPORT.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/INFRA-STATUS-ONE-PAGER.md | docs/00-SSOT/legacy_reports_archive/ |
| docs/00-SSOT/OPERATIONAL-VERIFICATION-REPORT.md | docs/00-SSOT/legacy_reports_archive/ |

---

## 2) 새로 생성한 파일 목록

| 파일 | 용도 |
|------|------|
| docs/00-SSOT/README.md | 00-SSOT 인덱스·v4 링크·아카이브 설명 |
| docs/00-SSOT/reports/README.md | “Moved to v4/reports” shim |
| scripts/v4/verify.ps1 | 5단계 검증 자동화, 로그 logs/v4/ |
| scripts/v4/README.md | 딸깍 5단계·params·PruneLegacy 주의 |
| scripts/infra/README.md | 실행 금지·v4/templates 정식·보관 |

---

## 3) 수정된 README/인덱스 목록

| 파일 | 변경 요약 |
|------|-----------|
| docs/README.md | 정식 = 00-SSOT/v4, 필독 링크·폴더 구조 갱신 |
| scripts/README.md | 정식 진입점 = v4, infra 실행 금지, 템플릿 v4 정식 |
| README.md (루트) | 상단 고정 3링크: 정식 문서·정식 배포·검증 |
| scripts/v4/core/ssot.ps1 | params 경로 → docs\00-SSOT\v4\params.yaml |
| scripts/v4/bootstrap.ps1 | params 경로 → docs\00-SSOT\v4\params.yaml |

---

## 4) 정식 문서/정식 스크립트 최종 경로 10줄 요약

1. **정식 SSOT 문서**: `docs/00-SSOT/v4/SSOT.md`
2. **정식 파라미터**: `docs/00-SSOT/v4/params.yaml`
3. **상태 계약**: `docs/00-SSOT/v4/state-contract.md`
4. **런북**: `docs/00-SSOT/v4/runbook.md`
5. **Evidence 스키마**: `docs/00-SSOT/v4/evidence.schema.md`
6. **리포트**: `docs/00-SSOT/v4/reports/` (drift.latest.md, audit.latest.md, history/)
7. **정식 배포 스크립트**: `scripts/v4/deploy.ps1`
8. **새 PC 준비**: `scripts/v4/bootstrap.ps1`
9. **5단계 검증**: `scripts/v4/verify.ps1`
10. **템플릿**: `scripts/v4/templates/` (정식). `scripts/infra/` 는 레거시 보관·실행 금지.

---

## 5) verify.ps1 실행 예시 및 기대 출력

**실행 예시**

```powershell
cd C:\academy
pwsh scripts/v4/verify.ps1
```

**기대 출력 (요약)**

- 각 단계별 `--- 1) bootstrap.ps1 ---` … `--- 4) deploy.ps1 (rerun, expect No-op) ---` 로그.
- 마지막에 **결과 표**:

| Step | Result | Detail |
|------|--------|--------|
| 1) bootstrap | OK | |
| 2) deploy -Plan | OK | Reports: docs/00-SSOT/v4/reports/ |
| 3) deploy -PruneLegacy | OK | |
| 4) deploy (No-op) | OK | No-op confirmed |
| 5) Evidence | - | docs/00-SSOT/v4/reports/, deploy stdout |

- 로그 파일 경로: `logs/v4/YYYYMMDD-HHMMSS-verify.log`
- 중간 실패 시 즉시 중단, 실패 지점·명령·`Log file: ...` 출력.
