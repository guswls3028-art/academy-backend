# SSOT v4 원테이크 패키지 — 구현 변경 보고

**일자:** 2026-02-27

---

## 변경된 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| **scripts/v4/core/reports.ps1** | **신규.** Save-DriftReport, Save-EvidenceReport, Save-VerifyReport. docs/00-SSOT/v4/reports/ + history/ 저장. |
| **scripts/v4/core/evidence.ps1** | Get-EvidenceSnapshot 추가, Show-Evidence가 Snapshot 호출 후 출력·반환. Convert-EvidenceToMarkdown 유지. |
| **scripts/v4/core/prune.ps1** | Get-PurgePlan, Invoke-PurgeAndRecreate 추가 (EventBridge→Queue→CE→JobDef 순 삭제, 선택적 PruneLegacy). |
| **scripts/v4/core/guard.ps1** | scripts/archive 경로 호출 시 throw. Assert 시 전체 호출 스택에서 infra/archive 검사. |
| **scripts/v4/deploy.ps1** | -PurgeAndRecreate, -DryRun 파라미터. reports.ps1 dot-source. Drift/Evidence 후 Save-* 호출. PurgeAndRecreate -DryRun 시 계획만 저장 후 종료. PurgeAndRecreate 시 Invoke-PurgeAndRecreate 후 전체 Ensure. Assert-NoLegacyScripts 호출. |
| **scripts/v4/bootstrap.ps1** | UTF-8 강제. aws --version 검사. 최소 describe(ec2 describe-vpcs) 권한 테스트. PowerShell 5+ 필수. |
| **scripts/v4/resources/api.ps1** | Confirm-APIHealth: 200이 아니면 throw (인프라 정렬 실패). |
| **scripts/v4/verify.ps1** | 종료 시 core 로드 후 Get-StructuralDrift, Get-EvidenceSnapshot 호출해 PASS/FAIL 표 작성 후 Save-VerifyReport로 verify.latest.md 저장. |
| **docs/00-SSOT/v4/V4-IMPLEMENTATION-SUMMARY.md** | reports 경로, PurgeAndRecreate, verify.latest.md, API health, bootstrap, archive guard 반영. |
| **docs/README.md** | 리포트 경로( drift/audit/verify, history) 링크 추가. |
| **scripts/README.md** | verify.latest.md, -PurgeAndRecreate/-DryRun, archive 실행 금지 명시. |

---

## 신규 생성 파일

| 파일 | 용도 |
|------|------|
| **scripts/v4/core/reports.ps1** | Drift/Evidence/Verify 리포트를 docs/00-SSOT/v4/reports/ 및 reports/history/에 저장. |
| **docs/00-SSOT/v4/reports/ONE-TAKE-IMPLEMENTATION-REPORT.md** | 본 보고서. |

---

## 동작 요약

1. **리포트 저장**  
   - deploy 실행 시(Plan 포함) Get-StructuralDrift 결과 → `drift.latest.md` 및 `reports/history/YYYYMMDD-HHmmss-drift.md`.  
   - deploy 종료 시 Evidence → `audit.latest.md` 및 `reports/history/...-audit.md`.  
   - verify.ps1 종료 시 검증 결과 → `verify.latest.md` 및 `reports/history/...-verify.md`.

2. **Purge 모드**  
   - `-PurgeAndRecreate -DryRun`: 삭제 예정 목록만 audit 형태로 저장 후 종료.  
   - `-PurgeAndRecreate`: SSOT EventBridge 규칙 비활성화 → Queue disable/delete → CE disable/delete → JobDef deregister 순 실행 후 전체 Ensure 재실행.

3. **API health**  
   - `/health` 응답이 200이 아니면 throw. 400 등은 인프라 정렬 실패로 처리.

4. **Bootstrap**  
   - 콘솔 UTF-8, aws --version, 최소 describe 권한 확인. 실패 시 명확한 메시지와 함께 exit.

5. **Archive/Infra 방지**  
   - deploy 진입 시 호출 스택에 `scripts\infra` 또는 `scripts\archive`가 있으면 즉시 throw.

---

## 미구현(선택 사항)

- **API/ Build/ ASG Ensure 격상**: 현재는 Confirm 수준 유지. 요구 시 SSOT에 AMI/InstanceProfile/UserData 등을 정의한 뒤 Ensure-API(재생성), Ensure-Build, Ensure-ASG(create-if-missing + 수렴)를 별도 작업으로 구현 가능.

---

## 검증 순서 (목표)

1. `pwsh scripts/v4/bootstrap.ps1`
2. `pwsh scripts/v4/deploy.ps1 -Plan` (AWS 변경 0, drift/audit 리포트 생성)
3. `pwsh scripts/v4/deploy.ps1`
4. `pwsh scripts/v4/deploy.ps1` (No-op)
5. `pwsh scripts/v4/deploy.ps1 -PruneLegacy -Plan` (삭제 후보만 표시)
6. `pwsh scripts/v4/verify.ps1` (PASS 시 verify.latest.md 생성)
