# 비상모드 / 정상모드 운영 정의

## 이 문서의 목적

이 문서는 안전장치(롤백 스크립트, 진단 도구, 장애 런북)의 **발동 조건**을 정의합니다.
안전장치는 항상 켜져 있는 운영 기본 모드가 아닙니다.

---

## 정상모드 (Normal Operator Mode)

운영자가 정상 컨디션으로 직접 운영하는 기본 상태.

**특징:**
- 운영자가 직접 코드 수정, 배포, 모니터링 수행
- git push → CI/CD 자동 배포 → 검증까지 운영자 주도
- 안전장치 스크립트는 **참고용**으로만 존재
- AI(Claude)는 개발 보조 역할

**이 모드에서 안전장치는:**
- 사용 가능하지만 강제되지 않음
- `run-ops-healthcheck.ps1`은 편의 도구로 자유롭게 사용
- 롤백 스크립트는 필요할 때만 사용
- 런북은 참고 문서

---

## 비상모드 (Emergency Recovery Mode)

운영자가 정상 판단이 어렵거나, 비전공자가 대리 수복하는 비상 상태.

**이 모드에서는 안전장치가 필수 절차가 됩니다.**

### 발동 조건 (하나라도 해당 시)

1. **운영자 선언:** 운영자가 "비상모드 전환"을 명시적으로 선언
2. **컨디션 저하:** 운영자가 질병, 과로, 수면 부족, 정신적 혼란 등으로 정상 판단이 어렵다고 스스로 인정
3. **응답 불가:** 운영자가 응답 불가 상태이고 서비스 장애가 진행 중
4. **대리 위임:** 운영자가 비전공자 지인/가족에게 대리 수복을 명시적으로 위임

### 비상모드에서의 행동 규칙

**비전공자 대리자 + AI(Claude) 조합:**

1. **먼저 상태 확인:**
   ```
   powershell -File scripts/v1/run-with-env.ps1 -- pwsh -File scripts/v1/run-ops-healthcheck.ps1
   ```
   → 결과를 AI에게 보여주고 판단 요청

2. **AI가 복구가 필요하다고 판단하면:**
   - API/Messaging 장애는 image rollback이 fail-closed이므로 운영자에게 즉시 연락하고
     새 immutable image roll-forward를 요청
   - AI/Tools처럼 runtime-isolated 서비스만 AI가 지정한 SHA와 스크립트를 그대로 실행
   - API/Messaging wrapper를 실행해도 `STATEFUL_IMAGE_ROLLBACK_BLOCKED` 이후 AWS
     변경 없이 종료되며 우회하지 않음

3. **절대 하지 말 것:**
   - 코드 수정 (git commit/push)
   - DB 직접 접근 (SQL 실행)
   - AWS 콘솔에서 리소스 삭제
   - deploy.ps1 실행

4. **할 수 있는 것:**
   - 상태 점검 스크립트 실행 (읽기 전용)
   - AI가 명시한 runtime-isolated rollback 스크립트만 실행
   - AI에게 결과 보여주고 다음 행동 질문
   - 운영자에게 연락 시도

### 해제 조건 (하나라도 해당 시)

1. **운영자 복귀:** 운영자가 정상 컨디션으로 복귀하고 "정상모드 전환" 선언
2. **장애 종료:** 서비스 상태가 정상화되고 `run-ops-healthcheck.ps1` 결과가 "✅ 정상"
3. **대리 권한 만료:** 운영자가 지정한 위임 시간이 경과
4. **운영자 직접 확인:** 운영자가 사후 확인 완료

**비상모드 해제 시 반드시:**
- `run-ops-healthcheck.ps1` 실행하여 전체 상태 확인
- 비상모드 중 수행한 작업 목록 운영자에게 전달
- 롤백이 있었다면 원인 분석은 운영자가 정상 복귀 후 수행

---

## 비상모드 대리자를 위한 3줄 요약

1. **상태 확인** → `run-ops-healthcheck.ps1` 실행 → 결과를 AI에게 보여줌
2. **AI 지시 따름** → API/Messaging이면 운영자에게 roll-forward 요청
3. **코드/DB/삭제는 절대 하지 않음** → 읽기 점검 + 허용된 runtime rollback만 가능

---

## 스크립트 목록 (비상모드에서 사용 가능)

| 스크립트 | 용도 | 위험도 |
|---------|------|--------|
| `run-ops-healthcheck.ps1` | 전체 상태 점검 | 없음 (읽기 전용) |
| `rollback-api.ps1` | Stateful rollback 정책 확인 | mutation 전 fail-closed |
| `rollback-messaging.ps1` | Stateful rollback 정책 확인 | mutation 전 fail-closed |
| `rollback-ai.ps1` | AI 워커 롤백 | 낮음 |
| `ecr-cleanup.py --verify` | ECR 상태 확인 | 없음 (읽기 전용) |

**절대 실행 금지 (비상모드):**

| 스크립트 | 이유 |
|---------|------|
| `deploy.ps1` | 인프라 프로비저닝 — 관리자 전용 |
| `ecr-cleanup.py --execute` | 이미지 삭제 — 운영자 판단 필요 |
| `git push` | 코드 배포 — 비전공자 금지 |
| `manage.py migrate` | DB 스키마 변경 — 운영자 전용 |
