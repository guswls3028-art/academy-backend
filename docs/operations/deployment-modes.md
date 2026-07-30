# 배포 방식 개요

**기준:** 실제 스크립트·워크플로우. 문서는 실행 방식과 일치하도록 유지한다.
**최종 갱신:** 2026-07-30

---

## 0. 프론트엔드 배포

프론트엔드(`frontend/` 레포)는 백엔드와 완전히 독립된 배포 파이프라인을 가진다.

| 항목 | 내용 |
|------|------|
| **트리거** | `git push origin main` (frontend 레포) |
| **배포 대상** | GitHub Actions가 검증한 bundle을 Wrangler direct upload로 Cloudflare Pages에 배포 |
| **스크립트** | backend 배포 스크립트로 프론트를 배포하지 않는다. |

---

## 1. 백엔드 배포 구조

- **이미지 빌드·ECR 푸시:** GitHub Actions만 수행 (`.github/workflows/v1-build-and-push-latest.yml`). 로컬/EC2 빌드 금지.
- **API 서버 반영 경로:**

| 경로 | 트리거 | 서버 반영 방식 | 속도 |
|------|--------|----------------|------|
| **CI 자동 배포** | main push → GitHub Actions | build-and-push → 상시 격리 development(실사용 smoke) → 임시 격리 preprod(전용 DB migration+health) → 운영 migration → deploy-api/messaging/ai/tools/video → verify-deployment | ~20~40분 |
| **수동 인프라 수렴** | `pwsh scripts/v1/deploy.ps1 -AwsProfile <approved-operator>` | 이미 검증·승격된 digest로 후보 env 준비 → 격리 preprod → 운영 env 승격 → API/worker/Batch/EventBridge/ALB 런타임 반영 | 20~30분 |

- **env·이미지 소스:** 운영은 SSM `/academy/api/env` → `/opt/api.env`, development는 버전 고정 `/academy/api/development/env`·`/academy/workers/development/env`, `academy_api_development` DB와 개발 전용 큐/R2를 사용한다. preprod는 릴리스마다 운영 env에서 새 Advanced SecureString 버전을 만들되 `/academy/api/preprod/db-credentials`의 전용 `academy_api_preprod_app` 역할로 DB 사용자·비밀번호를 교체하고 릴리스 ID를 함께 고정한다. 이미지는 완전 성공 `docs/reports/release-manifest.latest.json`의 `academy-api@sha256:...`를 사용한다.
- **API 역할 불변조건:** `/academy/api/env`의 `DJANGO_SETTINGS_MODULE`은 `apps.api.config.settings.prod`, `/academy/workers/env`는 `apps.api.config.settings.worker`여야 한다. 누락·교차 오염·API env 조회 실패 시 배포를 중단하며 workers env에서 API env를 합성하지 않는다.
- **격리 불변조건:** development와 preprod EC2는 운영 ASG/ALB에 등록하지 않는다. development는 inbound 없는 전용 보안그룹·instance profile·DB/큐/R2를 사용한다. preprod는 전용 instance profile, 정확한 SSM parameter version·릴리스 ID, 별도 DB·전용 DB 역할을 사용하며 그 역할의 운영 DB `CONNECT`가 거부되는지 실연결로 증명한다.

---

## 2. CI 자동 배포 (push=서버 반영)

main에 push하면 자동으로 서버 반영까지 완료된다:

1. GitHub Actions `v1-build-and-push-latest.yml` 트리거
2. lint, expand/contract migration guard와 smoke가 통과한 뒤 변경 감지 결과에 따라 필요한 이미지(base, api, video-worker, messaging-worker, ai-worker-cpu, tools-worker)만 linux/arm64 빌드해 ECR run-unique `:sha-*` 후보로 푸시한다. `:latest`는 이 시점에 움직이지 않는다. 자동 push는 contract migration을 차단하고, 이미 expand release가 완전히 배포된 contract만 주석과 사유를 기록한 뒤 명시적 `workflow_dispatch` 입력으로 허용한다. `workflow_dispatch` 또는 core/shared 변경은 전체 빌드.
3. `verify-api-development` job → 같은 release manifest의 API/Tools digest를 상시 격리 development에 blue/green 방식으로 배포한다. 전용 DB migration, 운영 DB·R2 접근 거부, 개발 큐/R2/Redis, `/healthz`, `/health`, 이미지 identity와 합성 XLSX/PPT/R2 실사용 smoke가 모두 통과해야 candidate를 active로 승격한다.
4. `verify-api-preprod` job → development를 통과한 API digest로 릴리스 고정 env 버전을 만들고 임시 격리 EC2 1대를 기동해 별도 DB에 migration을 적용한다. prod settings, DB 이름·전용 역할, 운영 DB CONNECT 거부, env version·release ID, `/healthz`, DB 포함 `/health`, 실제 CDN chain을 모두 확인한 뒤 종료한다.
5. preprod 성공 후에만 `run-migrations`가 운영 DB migration을 실행한다.
6. 모든 `deploy-api`, `deploy-messaging`, `deploy-ai`, `deploy-tools`, `deploy-video` job은 같은 development·preprod 성공 결과를 공통 선행조건으로 사용한다.
7. API Launch Template pin과 ASG rolling refresh를 실행한다. development, preprod 또는 임시 서버 cleanup 실패 시 어떤 운영 서비스도 변경하지 않는다.
8. 새 인스턴스 기동 → UserData로 ECR pull + 운영 SSM env 역할 검증 + docker run
9. `verify-deployment` job → API health, ASG 상태, tenant maintenance flag, 실제 digest 확인 + API 변경 시 학생 영상 playback chain smoke. 학생 계정 secret이 없으면 skip하지 않고 실패한다.
10. 모든 검증 성공 뒤에만 여섯 저장소의 `:latest`를 검증된 digest로 옮겨 exact readback하고 `release-manifest.latest.json`을 승격한다. 이미 같은 digest인 `:latest`는 성공한 no-op으로 처리한다. failed-job 재실행은 새 `run_attempt` 소유자로 공용 production mutation lock을 갱신하거나 조건부 재획득한 뒤 검증·승격을 계속하므로, 이전 attempt의 lock 해제 뒤에도 잠금 없이 compatibility alias를 변경하지 않는다.

**IAM:** 일반 CI는 장기 access key가 아니라 main 브랜치만 신뢰하는 GitHub OIDC 역할 `academy-gha-ecr-build`을 사용한다. development 권한은 별도 관리형 정책 `academy-gha-development-deploy`로 제한하고, development EC2는 `academy-api-development-role`, preprod EC2는 `academy-api-preprod-canary-role`을 사용한다. 사용자가 명시적으로 승인한 수동 작업은 이미 구성된 account-root 또는 master credential을 사용할 수 있지만, mutation guard 경고 이후에도 동일한 lock·development·preprod·migration·rolling-health·readback 게이트를 모두 지켜야 하며 비밀값은 출력하지 않는다.

---

## 3. 수동 인프라 수렴 (scripts/v1/deploy.ps1)

- **목적:** 인프라 변경(Launch Template, UserData, ASG, ALB, SSM, Batch 등)을 반영할 때.
- **실행:** `check-credentials.ps1`을 통과한 승인된 profile로 `pwsh scripts/v1/deploy.ps1 -AwsProfile <approved-operator>`
- **동작:** Bootstrap → Ensure-Network/ECR → 운영 env 후보 준비(무변경) → candidate SSM/별도 DB 격리 검증 → 운영 env 원자 승격 → API/worker/Batch/EventBridge/ALB 런타임 반영 → After-Deploy Verification
- **언제 써야 하는지:**
  - Launch Template, UserData, ASG, ALB, SSM 파라미터 등 인프라 설정 변경 시
  - 출시 전/후, 안정 반영이 필요할 때
  - "서버 상태를 정석 경로로 통째로 맞추고 싶을 때"

새 애플리케이션 digest를 처음 승격하는 용도로 사용하지 않는다. 새 후보는
먼저 GitHub Actions OIDC 경로의 persistent development와 isolated
preproduction을 통과해야 한다. account-root는 명시적으로 승인된 수동
작업에서만 경고와 함께 허용되며 게이트 우회 권한을 뜻하지 않는다.

**상세:** [formal-deploy.md](formal-deploy.md)

---

## 4. 주의사항

- **문서와 스크립트 불일치 금지.** 배포 설명은 실제 `scripts/v1/deploy.ps1`, `.github/workflows/v1-build-and-push-latest.yml` 기준으로만 기술한다.
- **멀티테넌트:** 어떤 배포 경로를 쓰든 tenant fallback·default tenant·tenant 없는 query·cross-tenant 노출은 금지.
- env는 SSM→/opt/api.env만 사용하며, development, preprod와 운영 parameter를 분리한다.
- 운영 API 서버에 후보 이미지나 후보 env를 먼저 적용하지 않는다. 상시 development 검토와 `run-api-preprod-canary.ps1`의 임시 격리 검증이 모두 성공한 뒤에만 운영 API/worker Launch Template·ASG, Batch job definition, EventBridge, ALB 또는 운영 컨테이너 변경이 허용된다.
- 운영 env만 변경된 경우에도 컨테이너를 제자리 재시작하지 않는다. env parameter version이 포함된 Launch Template로 ASG rolling refresh한다.

---

## 5. 검증 방법

| 목적 | 방법 |
|------|------|
| 배포 후 API·인프라 상태 | `run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport` 후 `run-deploy-verification.ps1 -AwsProfile default`. |
| 학생 영상 재생 경로 좁은 회귀 | `python scripts/post_deploy_smoke/video_playback_chain.py` |
| 성공 릴리스 digest와 서버 이미지 일치 | `release-manifest.latest.json`의 digest와 Launch Template, 실제 InService 컨테이너, Video Batch job definition을 `deploy-api-and-verify-workers.ps1`로 비교. |
| API health | API 공개 URL로 `/healthz`, `/health` 200 확인. |

---

## 6. 장애 시 확인 포인트

- `scripts/v1/deploy.ps1` stderr, `API_PREPROD_CANARY_PASS` 유무, ASG/ALB/Batch 상태, SSM `/academy/api/env` 존재·형식·prod settings module.
- CI deploy-* 또는 verify-deployment job 실패 시: GitHub Actions 로그 확인 → IAM 권한/ASG/ALB/Batch 상태 확인.
- health check 실패 시 `docker logs academy-api`.

---

## 7. 관련 문서

| 문서 | 내용 |
|------|------|
| `docs/operations/formal-deploy.md` | 수동 정식 배포 상세: 목적, 실행 방식, 검증, 주의. |
| `docs/operations/배포.md` | 인프라 부트스트랩 (RDS/SQS/EC2/IAM 처음부터). |
| `.github/workflows/v1-build-and-push-latest.yml` | CI 빌드·ECR·마이그레이션·서비스별 deploy·검증 흐름. |

---

## 8. 멀티테넌트 관련 금지 사항 (배포와 무관하게 적용)

- tenant fallback, default tenant, host 보정, tenant 추정 금지.
- tenant를 식별할 수 없는 상태에서 검증 성공으로 처리 금지.
- tenant context 없는 query, cross-tenant 조회 가능성, tenant 필터 누락 금지.
- env는 SSM→/opt/api.env만 사용. 운영 편의로 tenant isolation 약화 금지.
