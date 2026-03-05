# scripts/v4 — 정식 배포·검증

**딸깍 5단계** (새 PC에서 그대로 재현):

1. **bootstrap** — `pwsh scripts/v4/bootstrap.ps1`  
   AWS CLI, 인증, region, `docs/00-SSOT/v4/params.yaml` 존재 확인.

2. **deploy -Plan** — `pwsh scripts/v4/deploy.ps1 -Plan`  
   AWS 변경 없이 표/리포트만 출력. Drift·Evidence 확인.

3. **deploy -PruneLegacy** — `pwsh scripts/v4/deploy.ps1 -PruneLegacy`  
   SSOT 외 academy-* 리소스 정리 후 Ensure. **주의**: 삭제가 발생하므로 신중히 실행.

4. **deploy 재실행 (No-op)** — `pwsh scripts/v4/deploy.ps1`  
   변경 없이 완료되는지 확인. 출력에 "Idempotent: No changes required" 확인.

5. **Evidence 확인** — `docs/00-SSOT/v4/reports/` 및 deploy stdout의 Evidence 테이블.

---

## 자동 검증

한 번에 위 5단계를 실행:

```powershell
pwsh scripts/v4/verify.ps1
```

실패 시 즉시 중단되고, 실패 지점·명령·로그 경로를 출력. 로그는 `logs/v4/YYYYMMDD-HHMMSS-verify.log`.

---

## params.yaml

- **위치**: `docs/00-SSOT/v4/params.yaml`
- **수정**: 환경별 값(리전, 계정, VPC 등)만 변경. 스크립트는 이 파일만 참조.

---

## 주의

- **PruneLegacy**: SSOT에 없는 academy-* 리소스를 삭제합니다. 실행 전 Plan으로 후보를 확인하세요.
