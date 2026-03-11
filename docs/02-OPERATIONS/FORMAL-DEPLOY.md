# Formal Deploy (정식 배포)

**목적:** 안정 반영, 인프라/Launch Template/userdata/SSM 반영, 릴리즈·하루 마감용.

---

## 1. 목적

- **안정 반영:** 출시 전/후, 마감 시점 등 한 번에 확정 반영.
- **인프라 반영:** Launch Template, UserData, ASG, ALB, SSM `/academy/api/env`, RDS/Redis 확인, Batch CE/Queue 등.
- **릴리즈·마감용:** "지금 서버를 정석 경로로 통째로 맞추는" 배포.

---

## 2. 실행 방식

### 2.1 진입점

- **전체 인프라 + API 반영:**
  `pwsh scripts/v1/deploy.ps1 -AwsProfile default`
- **main push만으로 API 이미지 반영 (CI 자동):**
  main에 push → GitHub Actions `v1-build-and-push-latest.yml` 실행 → **build-and-push** 후 **deploy-api-refresh** job이 `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg` 실행 (MinHealthyPercentage=100, InstanceWarmup=300).
  즉, **push만 해도** CI가 ECR 푸시 후 API ASG instance refresh까지 수행한다. IAM 권한 적용 완료 (2026-03-11).

### 2.2 deploy.ps1 동작 순서 (요약)

1. Lock, Preflight, Drift 보고
2. Bootstrap(선택): SSM, SQS, RDS engine, ECR 등 Ensure
3. Ensure-Network, Ensure-ECR, Ensure-API-LaunchTemplate, Ensure-API-ASG, Ensure-API-Instance
4. **Ensure-API:** API Launch Template 갱신 후, LT drift 시 `start-instance-refresh` 호출
5. 새 인스턴스 기동 시 **UserData** 실행: ECR 로그인 → `docker pull` academy-api:latest → SSM `/academy/api/env` → `/opt/api.env` → `docker run -d ... --env-file /opt/api.env academy-api:latest`
6. Netprobe(선택), Evidence 저장, After-Deploy Verification(ASG desired/inService, ALB target health, Batch CE/Queue)

**관련 파일:** `scripts/v1/deploy.ps1`, `scripts/v1/resources/api.ps1` (Get-ApiLaunchTemplateUserData, Ensure-API-ASG, Ensure-API-Instance).

### 2.3 ASG / Launch Template / instance refresh 연결

- **Launch Template:** `Ensure-API-LaunchTemplate`에서 UserData에 ECR URI, SSM 파라미터명, `docker pull`/`docker run` 스크립트 삽입.
- **ASG:** academy-v1-api-asg가 해당 Launch Template 사용.
- **Instance refresh:** LT가 갱신되거나(subnet drift 등) 정책상 refresh가 필요할 때 `start-instance-refresh` 호출. 새 인스턴스가 뜨면 UserData로 최신 이미지·env 적용 후, 기존 인스턴스는 정책에 따라 순차 종료.

---

## 3. 특징

- **느리지만 정석.** 반영 범위가 넓고, 새 인스턴스 기동·검증 성격.
- **빌드는 하지 않음.** `-SkipBuild` 기본. 이미지는 GitHub Actions가 ECR에 푸시한 것을 사용.
- **실행 시간:** API health 대기(최대 300s), Netprobe(cold start 시 최대 600s) 등으로 20~25분 넘을 수 있음. CI/터미널 타임아웃 30분 이상 권장.

---

## 4. 언제 써야 하는지

- Launch Template, UserData, ASG, ALB, SSM 파라미터 등 **인프라 변경**을 반영할 때.
- **안정 반영**이 필요할 때(출시 전/후, 하루 마감).
- "한 번만 수동으로 정식 배포"하고 싶을 때.

> 일상적인 코드 변경은 `git push main` → CI 자동 배포로 충분하다. deploy.ps1은 인프라 변경이 있을 때만 사용.

---

## 5. 실행 후 검증

- **deploy.ps1 내장:** After-Deploy Verification에서 ASG desired/inService, ALB target health, Batch Video CE/Queue 상태 출력. 실패 시 경고.
- **수동 검증:** tenant·API 동작 확인이 필요하면
  `pwsh scripts/v1/run-qna-e2e-verify.ps1 -AwsProfile default`
- **이미지 digest:** `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api digest vs 서버 `docker inspect academy-api --format '{{.RepoDigests}}'`.

---

## 6. 멀티테넌트 관련

- env는 **SSM `/academy/api/env` → `/opt/api.env`** 만 사용. tenant 격리·폴백 정책 적용.
- tenant resolver, auth, middleware, worker, deployment 관련 수정 후에는 배포 후 검증(예: run-qna-e2e-verify) 필수. tenant fallback·default tenant 금지.

---

## 7. 관련 문서

- `docs/02-OPERATIONS/DEPLOYMENT-MODES.md` — 배포 방식 개요
- `docs/02-OPERATIONS/CI-CD-분석-및-보강안.md` — CI 빌드·deploy-api-refresh 흐름
- `.cursor/rules/07_deployment_orchestrator.mdc`
