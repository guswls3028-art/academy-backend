# V1 배포 스크립트 변경 이력

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 스크립트·배포·비용 변경 시 **.cursor/rules/** 내 해당 룰을 **적재적소에 항시 확인**한다.

**기준:** `scripts/v1/` 하위. 배포·검증에 영향을 주는 변경만 기술.

---

## 2026-03-05

### 자격증명·프로파일

| 파일 | 변경 |
|------|------|
| **core/aws.ps1** | `AWS_PROFILE`이 설정된 경우 모든 `aws` 호출에 `--profile`, (없을 때만) `--region`을 주입하는 `Get-AwsArgsWithProfile` 추가. `Invoke-Aws`, `Invoke-AwsJson`에서 해당 인자로 호출. Cursor/새 프로세스에서 `-AwsProfile default` 사용 시에도 동일 자격증명 적용. |

### Bootstrap·Preflight·SSM

| 파일 | 변경 |
|------|------|
| **core/bootstrap.ps1** | `/academy/workers/env`가 없을 때 repo root의 `.env`를 파싱해 필수 키로 JSON 생성 후 SSM SecureString(Base64)으로 넣는 `Invoke-BootstrapWorkersEnv` 추가. Bootstrap 시 RDS password 전에 실행. |
| **core/preflight.ps1** | SSM `/academy/workers/env` 존재 확인을 raw `aws ssm get-parameter` → `Invoke-AwsJson`으로 변경해 프로파일 적용. |
| **resources/ssm.ps1** | `Confirm-SSMEnv`에서 raw `aws` 호출 제거, `Invoke-AwsJson`으로 get-parameter 호출. |

### JobDef·Build

| 파일 | 변경 |
|------|------|
| **resources/jobdef.ps1** | `Register-JobDefFromJson`에서 `& aws batch register-job-definition ...` 제거, `Invoke-Aws`로 동일 인자 호출해 프로파일 적용. |
| **resources/build.ps1** | `New-BuildInstance`: Spot `run-instances` 후 인스턴스 0개면 온디맨드 `run-instances` 재시도(폴백). |

---

**참조:** 배포 절차·검증·최종 보고는 `V1-DEPLOYMENT-PLAN.md`, `V1-DEPLOYMENT-VERIFICATION.md`, `V1-FINAL-REPORT.md`.
