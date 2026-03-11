# 배포 방식 개요

**기준:** 실제 스크립트·워크플로우. 문서는 실행 방식과 일치하도록 유지한다.
**최종 갱신:** 2026-03-11

---

## 0. 프론트엔드 배포

프론트엔드(`frontend/` 레포)는 백엔드와 완전히 독립된 배포 파이프라인을 가진다.

| 항목 | 내용 |
|------|------|
| **트리거** | `git push origin main` (frontend 레포) |
| **배포 대상** | Cloudflare Pages (자동 빌드·배포) |
| **스크립트** | 불필요. `deploy-front.ps1`, `deploy.ps1 -DeployFront` 사용 금지. |

---

## 1. 백엔드 배포 구조

- **이미지 빌드·ECR 푸시:** GitHub Actions만 수행 (`.github/workflows/v1-build-and-push-latest.yml`). 로컬/EC2 빌드 금지.
- **API 서버 반영 경로:**

| 경로 | 트리거 | 서버 반영 방식 | 속도 |
|------|--------|----------------|------|
| **CI 자동 배포** | main push → GitHub Actions | build-and-push → **deploy-api-refresh** job → API ASG instance refresh → 새 인스턴스가 ECR latest pull | ~10분 (빌드+refresh) |
| **수동 정식 배포** | `pwsh scripts/v1/deploy.ps1 -AwsProfile default` | 전체 인프라 Ensure + API LT 갱신 + ASG instance refresh | 20~25분 |

- **env·이미지 소스:** SSM `/academy/api/env` → `/opt/api.env`, ECR `academy-api:latest`.

---

## 2. CI 자동 배포 (push=서버 반영)

main에 push하면 자동으로 서버 반영까지 완료된다:

1. GitHub Actions `v1-build-and-push-latest.yml` 트리거
2. 5개 이미지(base, api, video-worker, messaging-worker, ai-worker-cpu) linux/arm64 빌드 → ECR `:latest` 푸시
3. `deploy-api-refresh` job → `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg`
4. 새 API 인스턴스 기동 → UserData로 ECR pull + SSM→/opt/api.env + docker run

**IAM:** `academy-gha-ecr-build` 역할에 ECR 권한 + `autoscaling:StartInstanceRefresh` 등 적용 완료 (2026-03-11).

---

## 3. 수동 정식 배포 (deploy.ps1)

- **목적:** 인프라 변경(Launch Template, UserData, ASG, ALB, SSM, Batch 등)을 반영할 때.
- **실행:** `pwsh scripts/v1/deploy.ps1 -AwsProfile default`
- **동작:** Bootstrap → Ensure-Network/ECR/API-LT/API-ASG → instance refresh → After-Deploy Verification
- **언제 써야 하는지:**
  - Launch Template, UserData, ASG, ALB, SSM 파라미터 등 인프라 설정 변경 시
  - 출시 전/후, 안정 반영이 필요할 때
  - "서버 상태를 정석 경로로 통째로 맞추고 싶을 때"

**상세:** [FORMAL-DEPLOY.md](FORMAL-DEPLOY.md)

---

## 4. 주의사항

- **문서와 스크립트 불일치 금지.** 배포 설명은 실제 `scripts/v1/deploy.ps1`, `.github/workflows/v1-build-and-push-latest.yml` 기준으로만 기술한다.
- **멀티테넌트:** 어떤 배포 경로를 쓰든 tenant fallback·default tenant·tenant 없는 query·cross-tenant 노출은 금지.
- env는 SSM→/opt/api.env만 사용한다.

---

## 5. 검증 방법

| 목적 | 방법 |
|------|------|
| 배포 후 API·인프라 상태 | deploy.ps1 출력의 After-Deploy Verification. 필요 시 `run-qna-e2e-verify.ps1`. |
| CI 빌드 digest와 서버 이미지 일치 | `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api digest vs 서버 `docker inspect academy-api --format '{{.RepoDigests}}'`. |
| API health | ALB DNS 또는 API 공개 URL로 `/health` 200 확인. |

---

## 6. 장애 시 확인 포인트

- deploy.ps1 stderr, ASG/ALB/Batch 상태, SSM `/academy/api/env` 존재·형식.
- CI deploy-api-refresh job 실패 시: GitHub Actions 로그 확인 → IAM 권한 확인.
- health check 실패 시 `docker logs academy-api`.

---

## 7. 관련 문서

| 문서 | 내용 |
|------|------|
| `docs/02-OPERATIONS/FORMAL-DEPLOY.md` | 수동 정식 배포 상세: 목적, 실행 방식, 검증, 주의. |
| `docs/02-OPERATIONS/CI-CD-분석-및-보강안.md` | CI 빌드·ECR·deploy-api-refresh 흐름. |
| `.cursor/rules/07_deployment_orchestrator.mdc` | 배포 진입점 구분. |
| `.cursor/rules/09_multitenant_isolation.mdc` | 멀티테넌트 격리·배포 검증 원칙. |

---

## 8. 멀티테넌트 관련 금지 사항 (배포와 무관하게 적용)

- tenant fallback, default tenant, host 보정, tenant 추정 금지.
- tenant를 식별할 수 없는 상태에서 검증 성공으로 처리 금지.
- tenant context 없는 query, cross-tenant 조회 가능성, tenant 필터 누락 금지.
- env는 SSM→/opt/api.env만 사용. 운영 편의로 tenant isolation 약화 금지.
