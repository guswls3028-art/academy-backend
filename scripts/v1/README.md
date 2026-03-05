# scripts/v1 — 정식 배포·검증 (풀셋팅 v1)

**딸깍 5단계** (새 PC에서 그대로 재현):

1. **bootstrap** — `pwsh scripts/v1/bootstrap.ps1`  
   AWS CLI, 인증, region, `docs/00-SSOT/v1/params.yaml` 존재 확인.

2. **deploy -Plan** — `pwsh scripts/v1/deploy.ps1 -Plan`  
   AWS 변경 없이 표/리포트만 출력. Drift·Evidence 확인.

3. **deploy -PruneLegacy** — `pwsh scripts/v1/deploy.ps1 -PruneLegacy`  
   SSOT 외 academy-* 리소스 정리 후 Ensure. **주의**: 삭제가 발생하므로 신중히 실행.

4. **deploy 재실행 (No-op)** — `pwsh scripts/v1/deploy.ps1`  
   변경 없이 완료되는지 확인. 출력에 "Idempotent: No changes required" 확인.

5. **Evidence 확인** — `docs/00-SSOT/v1/reports/` 및 deploy stdout의 Evidence 테이블.

---

## 자동 검증

한 번에 위 5단계를 실행:

```powershell
pwsh scripts/v1/verify.ps1
```

실패 시 즉시 중단되고, 실패 지점·명령·로그 경로를 출력. 로그는 `logs/v1/YYYYMMDD-HHMMSS-verify.log`.

---

## params.yaml

- **위치**: `docs/00-SSOT/v1/params.yaml`
- **수정**: 환경별 값(리전, 계정, VPC 등)만 변경. 스크립트는 이 파일만 참조.
- **API ASG max**: 2 고정 (solo dev, medium reliability).

---

## 주의

- **PruneLegacy**: SSOT에 없는 academy-* 리소스를 삭제합니다. 실행 전 Plan으로 후보를 확인하세요.

---

## Cursor / 새 셸에서 인증 에러 날 때

- **원인**: `$env:AWS_ACCESS_KEY_ID` 등은 현재 터미널 세션에만 적용됨. Cursor가 새 프로세스로 실행하면 인증이 없음.
- **진단**: `pwsh scripts/v1/aws-diagnose.ps1` — credential source·에러 메시지 확인.
- **해결**  
  - **방법 1**: `aws configure` 로 default 프로파일 저장 → 어떤 셸에서든 동작.  
  - **방법 2**: named 프로파일(또는 SSO) 사용 시 `pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile prod`, `pwsh scripts/v1/verify.ps1 -AwsProfile prod`.
- **상세**: [docs/00-SSOT/v1/AWS-CURSOR-SETUP.md](../../docs/00-SSOT/v1/AWS-CURSOR-SETUP.md)
