# scripts/v1 — 정식 배포·검증 (풀셋팅 v1)

> **ACTIVE:** 이 문서는 현재 유효합니다.
>  
> **Authoritative docs:** `docs/ssot/params.yaml`, `docs/infrastructure/`, `docs/README.md`
>  
> **Alias policy:** `docs/ssot/path-alias-policy.md`
>  
> **경고:** `docs/v1/...` 경로 표기는 stale 별칭일 수 있으므로 사용하지 않습니다.

**딸깍 5단계** (새 PC에서 그대로 재현):

1. **bootstrap** — `pwsh scripts/v1/bootstrap.ps1`  
   AWS CLI, 인증, region, `docs/ssot/params.yaml` 존재 확인.

2. **deploy -Plan** — `pwsh scripts/v1/deploy.ps1 -Plan -AwsProfile default`
   AWS 변경 없이 표/리포트만 출력. Drift·Evidence 확인.

3. **deploy -PruneLegacy** — `pwsh scripts/v1/deploy.ps1 -PruneLegacy -AwsProfile default`
   SSOT 외 academy-* 리소스 정리 후 Ensure. **주의**: 삭제가 발생하므로 신중히 실행.

4. **deploy 재실행 (No-op)** — `pwsh scripts/v1/deploy.ps1 -AwsProfile default`
   변경 없이 완료되는지 확인. 출력에 "Idempotent: No changes required" 확인.

5. **Evidence 확인** — `docs/reports/` 및 deploy stdout의 Evidence 테이블.

---

## 자동 검증

한 번에 위 5단계를 실행:

```powershell
pwsh scripts/v1/verify.ps1 -AwsProfile default
```

실패 시 즉시 중단되고, 실패 지점·명령·로그 경로를 출력. 로그는 `logs/v1/YYYYMMDD-HHMMSS-verify.log`.
production backend deploy/worker 변경 후에는 `run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport`와 `run-deploy-verification.ps1 -AwsProfile default`를 이어서 실행한다.

---

## legacy deploy cron 정리

구 hot/rapid deploy cron은 정식 배포 경로가 아니다. 서버에 과거 cron 잔재가 의심될 때만 cleanup 전용 스크립트를 사용한다.

```powershell
pwsh scripts/v1/disable-legacy-deploy-crons.ps1 -Action Status -AwsProfile default
pwsh scripts/v1/disable-legacy-deploy-crons.ps1 -Action Off -AwsProfile default
```

---

## params.yaml

- **위치**: `docs/ssot/params.yaml`
- **수정**: 환경별 값(리전, 계정, VPC 등)만 변경. 스크립트는 이 파일만 참조.
- **API ASG 용량**: 평시 min/desired=2, max=3. 배포 시 CI도 2대 이상 ALB healthy 기준을 유지하고, CPU target tracking이 평상시 자동 증감/복귀를 담당한다.
- **ECR 이미지**: GitHub Actions가 6개 repo(`academy-base`, `academy-api`, `academy-video-worker`, `academy-messaging-worker`, `academy-ai-worker-cpu`, `academy-tools-worker`)를 빌드·푸시한다. `deploy.ps1`은 로컬 빌드 없이 ECR `:latest`를 pull/refresh한다.

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
- **상세**: [docs/infrastructure/deployment-architecture.md](../../docs/infrastructure/deployment-architecture.md)
