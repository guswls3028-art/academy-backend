# scripts/archive/v4 — 레거시 v4 배포·검증 (실행 금지·참고용)

**정식은 scripts/v1을 사용한다.** 이 폴더는 아카이브용. 필요 시 참고만 하고 실행하지 말 것.

---

**딸깍 5단계** (과거 v4 기준, 참고용):

1. **bootstrap** — `pwsh scripts/archive/v4/bootstrap.ps1`  
   AWS CLI, 인증, region, `docs/00-SSOT/archive/v4/params.yaml` 존재 확인.

2. **deploy -Plan** — `pwsh scripts/archive/v4/deploy.ps1 -Plan`  
   AWS 변경 없이 표/리포트만 출력. Drift·Evidence 확인.

3. **deploy -PruneLegacy** — `pwsh scripts/archive/v4/deploy.ps1 -PruneLegacy`  
   SSOT 외 academy-* 리소스 정리 후 Ensure. **주의**: 삭제가 발생하므로 신중히 실행.

4. **deploy 재실행 (No-op)** — `pwsh scripts/archive/v4/deploy.ps1`  
   변경 없이 완료되는지 확인. 출력에 "Idempotent: No changes required" 확인.

5. **Evidence 확인** — `docs/00-SSOT/archive/v4/reports/` 및 deploy stdout의 Evidence 테이블.

---

## params.yaml

- **위치**: `docs/00-SSOT/archive/v4/params.yaml`
- **수정**: 환경별 값(리전, 계정, VPC 등)만 변경. 스크립트는 이 파일만 참조.

---

**실행 금지.** 정식 배포·검증은 `scripts/v1/deploy.ps1`, `scripts/v1/verify.ps1` 만 사용한다.
