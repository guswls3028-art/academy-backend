# 배포 방식 개요 (Formal Deploy vs Rapid Deploy)

**기준:** 실제 스크립트·워크플로우. 문서는 실행 방식과 일치하도록 유지한다.

---

## 1. 현재 실제 배포 구조

- **이미지 빌드·ECR 푸시:** GitHub Actions만 수행 (`.github/workflows/v1-build-and-push-latest.yml`). 로컬/EC2 빌드 금지.
- **API 서버에 코드 반영하는 경로는 두 가지다.**

| 경로 | 트리거 | 서버 반영 방식 | 속도 |
|------|--------|----------------|------|
| **Formal Deploy** | (1) `scripts/v1/deploy.ps1` 실행, (2) main push 시 CI의 **deploy-api-refresh** job | API ASG **instance refresh** → 새 인스턴스 기동 → UserData로 ECR pull, SSM→/opt/api.env, docker run | 느림 (수 분~20분) |
| **Rapid Deploy** | `api-auto-deploy-remote.ps1 -Action On` 후 main 변경 시 서버 **cron**(2분마다) | **기존 인스턴스**에서만: git main 감지 → `deploy_api_on_server.sh` → ECR pull, SSM→/opt/api.env, docker stop/rm/run | 빠름 (CI 푸시 후 최대 2분) |

- **env·이미지 소스:** 두 방식 모두 **SSM `/academy/api/env` → `/opt/api.env`**, **ECR academy-api:latest**. 멀티테넌트 격리·env 정책은 동일하다.

---

## 2. Formal Deploy vs Rapid Deploy 비교표

| 항목 | Formal Deploy | Rapid Deploy |
|------|---------------|--------------|
| **목적** | 안정 반영, 인프라/Launch Template/userdata/SSM 반영, 릴리즈·마감용 | 개발 중 빠른 코드 반영, 잦은 수정 대응 |
| **속도** | 느림 (instance refresh 수 분~20분, deploy.ps1 전체 20~25분+) | 빠름 (main 변경 감지 후 최대 2분, 컨테이너만 교체) |
| **반영 범위** | API LT, ASG, ALB, Batch, RDS/Redis 확인, Netprobe 등 넓음. 새 인스턴스 기동. | API 컨테이너만. 인프라/UserData/LT 변경 없음. |
| **사용 대상** | 출시·인프라 변경·안정 반영 담당자 | 개발 중 빠르게 API만 반영하려는 개발자 |
| **위험** | 인프라 전반 변경. 실수 시 영향 큼. | 인프라 미반영. LT/SSM 변경은 Formal 또는 수동 반영 필요. |
| **검증 방식** | deploy.ps1 내 After-Deploy Verification(ASG, ALB target, Batch CE/Queue). 필요 시 `run-qna-e2e-verify.ps1`. | deploy_api_on_server.sh 내 `/healthz` 1회. 필요 시 수동 E2E. |
| **사용 시점** | 인프라 변경 후, 출시 전/후, 하루 마감 최종 반영, “전체 정석 배포”가 필요할 때 | 개발 중 코드만 자주 바꿀 때, API 컨테이너만 반영하면 될 때 |
| **ON/OFF 가능 여부** | 없음. 실행 시 1회 수행. CI는 main push마다 deploy-api-refresh 실행. | **ON/OFF 있음.** `-Action On` / `-Action Off`로 2분 감지 cron 켜기/끄기. |
| **멀티테넌트 관련** | env는 SSM→/opt/api.env만 사용. tenant 격리·검증 원칙 동일. | 동일. Rapid Deploy도 SSM→/opt/api.env만 사용. **격리 완화·tenant fallback 금지.** |

---

## 3. 언제 무엇을 써야 하는지

- **Formal Deploy를 써야 하는 경우**
  - Launch Template, UserData, ASG, ALB, SSM 파라미터, 인프라 설정 변경을 반영할 때
  - 출시 전/후, 하루 마감 등 **안정 반영**이 필요할 때
  - “지금 서버 상태를 정석 경로로 통째로 맞추고 싶을 때”  
  → **실행:** `pwsh scripts/v1/deploy.ps1 -AwsProfile default`  
  → **또는:** main에 push하면 CI가 ECR 푸시 후 **deploy-api-refresh**로 API ASG instance refresh까지 수행 (이미지만 반영하는 Formal 경로).

- **Rapid Deploy를 써야 하는 경우**
  - **개발 중** API 코드만 자주 수정하고, **빠르게** 서버에 반영하고 싶을 때 (instance refresh 대기 없이 컨테이너만 교체)
  - 인프라/SSM/UserData 변경은 없고, **이미지만** 최신으로 갈아끼우면 될 때  
  → **실행:** `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default` 후 main에 push.  
  → **작업 종료 시 반드시:** `-Action Off`로 Rapid Deploy 끄기. auto push / Rapid Deploy ON 상태 방치 금지.

- **Rapid Deploy로 대체하면 안 되는 경우**
  - Launch Template, SSM `/academy/api/env`, ASG, ALB 등 **인프라·env 변경**을 반영해야 할 때는 Formal Deploy(deploy.ps1 또는 CI refresh) 또는 env 반영 후 수동 1회 `-Action Deploy` 등으로 반영해야 함.

---

## 4. 주의사항

- **문서와 스크립트 불일치 금지.** 배포 설명은 실제 `scripts/v1/deploy.ps1`, `api-auto-deploy-remote.ps1`, `deploy_api_on_server.sh`, `.github/workflows/v1-build-and-push-latest.yml` 기준으로만 기술한다.
- **멀티테넌트:** 어떤 배포 경로를 쓰든 tenant fallback·default tenant·tenant 없는 query·cross-tenant 노출은 금지. 배포 후 tenant 관점 검증(예: run-qna-e2e-verify) 유지.
- **Rapid Deploy 중에도** tenant isolation 원칙은 완화하지 않는다. env는 SSM→/opt/api.env만 사용한다.

---

## 5. 검증 방법

| 목적 | 방법 |
|------|------|
| Formal 배포 후 API·인프라 상태 | deploy.ps1 출력의 After-Deploy Verification. 필요 시 `run-qna-e2e-verify.ps1`. |
| Rapid 배포 후 최신 반영 여부 | `api-auto-deploy-remote.ps1 -Action Status` → 마지막 배포 정보. 서버에서 `cat /home/ec2-user/.academy-rapid-deploy-last`, `docker inspect academy-api --format '{{.RepoDigests}}'`. |
| CI 빌드 digest와 서버 이미지 일치 | `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api digest vs 서버 RepoDigests. |

---

## 6. 장애 시 확인 포인트

- **Formal:** deploy.ps1 stderr, ASG/ALB/Batch 상태, SSM `/academy/api/env` 존재·형식. `.cursor/rules/06_incident_analysis.mdc` 참고.
- **Rapid:** 서버 로그 `/home/ec2-user/auto_deploy.log`, `-Action Status` 출력, SSM Send-Command 실패 시 해당 인스턴스에서 `bash scripts/deploy_api_on_server.sh` 수동 실행해 에러 확인. health check 실패 시 `docker logs academy-api`.

---

## 7. 관련 문서

| 문서 | 내용 |
|------|------|
| `docs/02-OPERATIONS/FORMAL-DEPLOY.md` | Formal Deploy 상세: 목적, 실행 방식, 검증, 주의. |
| `docs/02-OPERATIONS/RAPID-DEPLOY.md` | Rapid Deploy 상세: ON/OFF, 2분 감지, 명령, 실패 로그. |
| `docs/02-OPERATIONS/Rapid-Deploy-사용법.md` | Rapid Deploy 요약·명령 (RAPID-DEPLOY.md와 연결). |
| `docs/02-OPERATIONS/정석-배포-vs-원격-배포-비교.md` | Formal vs Rapid 비교 요약. |
| `docs/02-OPERATIONS/CI-CD-분석-및-보강안.md` | CI 빌드·ECR·deploy-api-refresh 흐름. |
| `.cursor/rules/07_deployment_orchestrator.mdc` | 배포 진입점·Formal vs Rapid 구분. |
| `.cursor/rules/09_multitenant_isolation.mdc` | 멀티테넌트 격리·배포 검증 원칙. |

---

## 8. 멀티테넌트 관련 금지 사항 (배포와 무관하게 적용)

- tenant fallback, default tenant, host 보정, tenant 추정 금지.
- tenant를 식별할 수 없는 상태에서 검증 성공으로 처리 금지.
- tenant context 없는 query, cross-tenant 조회 가능성, tenant 필터 누락 금지.
- 배포 경로(Formal/Rapid)와 관계없이 env는 SSM→/opt/api.env만 사용. 운영 편의로 tenant isolation 약화 금지.
