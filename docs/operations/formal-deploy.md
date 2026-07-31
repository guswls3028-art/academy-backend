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
  `pwsh scripts/v1/deploy.ps1 -AwsProfile <approved-operator>`
- **main push만으로 이미지 반영 (CI 자동):**
  main에 push → GitHub Actions `v1-build-and-push-latest.yml` 실행 → **build-and-push** → **verify-api-development(상시 격리 runtime의 실사용 smoke)** → **verify-api-preprod(전용 DB migration+health)** → 필요한 경우 **run-migrations(운영 DB)** → **deploy-api / deploy-messaging / deploy-ai / deploy-tools / deploy-video** → **verify-deployment**.
  즉, **push만 해도** CI가 ECR 푸시, 마이그레이션, 각 서비스 배포, health/ASG/tenant maintenance/video-chain 검증까지 수행한다.
  `run-migrations`는 실행 직전에 SSM `/academy/api/env`를 `/opt/api.env`로 원자적으로 갱신한 뒤 새 digest 이미지로 실행한다. 인스턴스에 남은 이전 env 파일을 재사용하지 않는다.

### 2.2 scripts/v1/deploy.ps1 동작 순서 (요약)

`scripts/v1/deploy.ps1`은 이미 검증·승격된 digest로 인프라를 수렴시키는 경로다. 새
애플리케이션 후보를 처음 운영에 올리는 경로가 아니며, 새 digest는 먼저
GitHub Actions의 persistent development와 isolated preproduction을 모두
통과해야 한다.

사전 조건으로 `/academy/r2/preprod/credentials`에 production key와 다른
video bucket Object Read/List 전용 R2 credential이 있어야 한다. credential의
`ACCESS_MODE=read-only` 선언만 신뢰하지 않고 Cloudflare 권한 readback을
배포 증거에 포함한다.

1. AWS mutation 전에 `assert-production-source-freshness.ps1`이 clean `main`, 최신 `origin/main`, complete/successful release manifest와 그 `gitSha`의 선조 관계를 검증한다. dirty/detached/stale/divergent source는 lock table 접근 전 실패한다.
2. production lock, Preflight, Drift 보고
3. Bootstrap(선택): SSM, SQS, RDS engine, ECR 등 Ensure. ECR은 immutable SHA + latest 단일 exclusion + scan-on-push를 exact readback한다.
4. Ensure-Network/ECR/IAM 후 운영 env 후보를 메모리에서 준비한다. 이 단계에서는 `/academy/api/env`와 `/academy/workers/env`를 쓰지 않는다.
5. 성공 release manifest의 API/Tools digest를 버전 고정 development env에 발행하고 `deploy-api-development.ps1`의 blue/green 실사용 gate를 실행한다. 이전 active instance는 새 후보가 migration, 운영자원 denial, health, image identity, XLSX/PPT/R2 smoke를 통과하기 전까지 유지한다.
6. 같은 API digest의 preprod env를 만들 때 전용 DB 역할로 교체하고 production Django/tenant signing secret, SOLAPI, Toss/billing, 외부 AI, VAPID, 정적 AWS credential을 제거한다. production R2 key도 제거하고 `/academy/r2/preprod/credentials`의 bucket-scoped read-only key로 교체한다. 격리 EC2에서 migration, prod settings, parameter version·릴리스 ID, DB 이름·역할, 운영 DB CONNECT 거부, `/healthz`, `/health`, CDN playback을 검증한다.
7. 두 격리 검증 성공 후에만 운영 API/worker env를 승격한다. 실패하면 worker ASG, Batch job definition, EventBridge, ALB를 포함한 운영 런타임 반영을 시작하지 않는다.
8. 검증된 env 승격 뒤 worker/Batch/EventBridge/ALB와 **Ensure-API**를 순서대로 수렴한다. env parameter version을 포함한 API Launch Template가 ASG rolling refresh를 유도하며, 운영 컨테이너 제자리 재시작은 정식 경로에서 사용하지 않는다.
9. 새 인스턴스 기동 시 **UserData** 실행: ECR 로그인 → 검증된 release manifest digest pull → SSM 역할 검증 → `/opt/api.env` → digest-pinned `docker run`
10. Netprobe(선택), Evidence 저장, After-Deploy Verification(ASG desired/inService, ALB target health, Batch CE/Queue)

**관련 파일:** `scripts/v1/deploy.ps1`, `scripts/v1/resources/api.ps1` (Get-ApiLaunchTemplateUserData, Ensure-API-ASG, Ensure-API-Instance).

### 2.3 ASG / Launch Template / instance refresh 연결

- **Launch Template:** `Ensure-API-LaunchTemplate`에서 UserData에 ECR URI, SSM 파라미터명, `docker pull`/`docker run` 스크립트 삽입.
- **ASG:** academy-v1-api-asg가 해당 Launch Template 사용.
- **Instance refresh:** LT가 갱신되거나(subnet drift 등) 정책상 refresh가 필요할 때 `start-instance-refresh` 호출. 새 인스턴스가 뜨면 UserData로 최신 이미지·env 적용 후, 기존 인스턴스는 정책에 따라 순차 종료.

---

## 3. 특징

- **느리지만 정석.** 반영 범위가 넓고, 새 인스턴스 기동·검증 성격.
- **빌드는 하지 않음.** `-SkipBuild` 기본. 이미지는 GitHub Actions가 ECR에 푸시한 것을 사용.
- **source가 배포 입력.** production 실행은 clean·최신 `main`만 허용한다. feature branch나 로컬 수정으로 인프라를 직접 반영하지 않는다.
- **실행 시간:** API health 대기(최대 300s), Netprobe(cold start 시 최대 600s) 등으로 20~25분 넘을 수 있음. CI/터미널 타임아웃 30분 이상 권장.

---

## 4. 언제 써야 하는지

- Launch Template, UserData, ASG, ALB, SSM 파라미터 등 **인프라 변경**을 반영할 때.
- **안정 반영**이 필요할 때(출시 전/후, 하루 마감).
- "한 번만 수동으로 정식 배포"하고 싶을 때.

> 일상적인 코드 변경은 `git push main` → CI 자동 배포로 충분하다. `scripts/v1/deploy.ps1`은 인프라 변경이 있을 때만 사용.
> 실행 전 `check-credentials.ps1`에서 identity를 확인한다. 일반 배포는
> GitHub Actions OIDC 또는 최소권한 운영자 역할을 사용한다. 사용자가
> account-root를 명시적으로 허용한 수동 작업은 경고 후 실행할 수 있지만,
> 비밀값을 출력하거나 development/preprod/rolling-health/readback 게이트를
> 줄이거나 건너뛰면 안 된다.

---

## 5. 실행 후 검증

- **`scripts/v1/deploy.ps1` 내장:** After-Deploy Verification에서 ASG desired/inService, ALB target health, Batch Video CE/Queue 상태를 확인한다. strict 기본 경로는 실패 시 nonzero로 종료하며, 명시적 `-RelaxedValidation`은 비운영 진단에서만 경고로 계속할 수 있다.
- **수동 검증:** `run-deploy-verification.ps1`은 read-only 인프라·health·drift·
  런타임 이미지 증빙을 수집한다. 인증 CRUD와 AI/Messaging enqueue→consume은
  실행하지 않으므로, 변경 도메인의 동작은
  `pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport`
  또는 해당 E2E/provider·worker 로그로 별도 확인한 뒤
  `pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default` 결과와 함께 판단한다.
  특정 QnA 회귀만 좁게 재확인할 때는 `pwsh scripts/v1/run-qna-e2e-verify.ps1 -AwsProfile default`
  학생 영상 재생 경로만 좁게 재확인할 때는 `python scripts/post_deploy_smoke/video_playback_chain.py`
- **이미지 digest:** `docs/reports/release-manifest.latest.json`은 배포·런타임 검증까지 성공한 6개 이미지의 유일한 수동 배포 입력이다. `deploy-api-and-verify-workers.ps1`이 LT/Batch 설정뿐 아니라 실제 InService 컨테이너의 `RepoDigests`까지 비교한다.

---

## 6. 멀티테넌트 관련

- env는 **SSM `/academy/api/env` → `/opt/api.env`** 만 사용. tenant 격리·폴백 정책 적용.
- tenant resolver, auth, middleware, worker, deployment 관련 수정 후에는 배포 후 검증(예: run-deploy-verification) 필수. tenant fallback·default tenant 금지.

---

## 7. 관련 문서

- `docs/operations/deployment-modes.md` — 배포 방식 개요
- `docs/operations/배포.md` — 인프라 부트스트랩 (RDS/SQS/EC2/IAM)
- `.github/workflows/v1-build-and-push-latest.yml` — CI 빌드·마이그레이션·서비스별 deploy·검증 흐름
- `docs/operations/github-governance.md` — branch ruleset, environment 승인, Actions/secret 제어면
