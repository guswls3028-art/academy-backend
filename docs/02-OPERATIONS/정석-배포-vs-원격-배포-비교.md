# Formal Deploy vs Rapid Deploy 비교

**작성일:** 2026-03-09  
**2트랙 개요·비교표:** [DEPLOYMENT-MODES.md](DEPLOYMENT-MODES.md)

---

## 1. 결론: **트리거·반영 범위만 다름, env·이미지 소스는 동일**

| 구분 | Formal Deploy | Rapid Deploy |
|------|---------------|--------------|
| **진입점** | `pwsh scripts/v1/deploy.ps1` 또는 main push 시 CI의 **deploy-api-refresh** | `api-auto-deploy-remote.ps1 -Action On` 시 2분마다 cron, 또는 `-Action Deploy` 1회 |
| **이미지** | **ECR** pull | **ECR** pull (동일) |
| **env 파일** | SSM → **/opt/api.env** | SSM → **/opt/api.env** (동일) |
| **빌드** | 없음 (CI만) | **없음** (서버에서 빌드 없음) |
| **트리거** | deploy.ps1 → instance refresh / CI push → deploy-api-refresh | cron 2분마다 main 변경 시 또는 수동 Deploy |
| **반영 범위** | API LT, ASG, 새 인스턴스 기동 | **기존 인스턴스**에서 API 컨테이너만 재시작 |

---

## 2. Formal Deploy 흐름 (deploy.ps1 / CI)

1. `scripts/v1/deploy.ps1` 실행 (이미지 빌드 없음, `-SkipBuild` 기본).
2. SSM `/academy/api/env` 갱신, Launch Template UserData 갱신 (SSM → `/opt/api.env`, ECR 이미지 URI로 `docker pull` + `docker run --env-file /opt/api.env`).
3. API ASG **instance refresh** 시작 → 새 인스턴스가 뜨면 UserData 실행:
   - Docker 설치, ECR 로그인, `docker pull <ECR URI>`, SSM으로 `/opt/api.env` 생성, `docker run -d --env-file /opt/api.env <ECR URI>`.
4. 기존 인스턴스는 refresh 정책에 따라 순차 종료. **인스턴스에는 Git 레포가 없음.**

**관련 파일:** `scripts/v1/deploy.ps1`, `scripts/v1/resources/api.ps1` (Get-ApiLaunchTemplateUserData).

---

## 3. Rapid Deploy 흐름 (api-auto-deploy-remote.ps1) — 결과물(env·이미지)은 Formal과 동일

1. **Action Status:** SSM으로 해당 인스턴스에 `crontab -l` 실행 → crontab 상태만 확인.
2. **Action Off:** SSM으로 `git fetch` + `git reset --hard origin/main` 후 `scripts/auto_deploy_cron_off.sh` 실행 → crontab에서 deploy 관련 라인 제거.
3. **Action On:** SSM으로 (레포 없으면 clone) `git fetch` + `git reset --hard origin/main` 후 `scripts/auto_deploy_cron_on.sh` 실행 → 2분마다 main 변경 시 `deploy_api_on_server.sh` 실행하는 cron 등록.
4. **Action Deploy:** SSM으로 동일하게 repo 준비 후 `scripts/deploy_api_on_server.sh` **1회** 실행.

**deploy_api_on_server.sh (리팩토링 후 — 정석과 동일):**

- SSM `/academy/api/env` 조회 → **/opt/api.env** 에 저장 (정석 UserData와 동일 경로).
- **docker pull** ECR 이미지 (`809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest`). **서버 빌드 없음.**
- `docker stop/rm academy-api`, `docker run -d --restart unless-stopped --name academy-api -p 8000:8000 --env-file /opt/api.env <ECR_URI>`.
- `docker image prune -f`.

**관련 파일:** `scripts/v1/api-auto-deploy-remote.ps1`, `scripts/deploy_api_on_server.sh`, `scripts/auto_deploy_cron_on.sh`, `scripts/auto_deploy_cron_off.sh`.

---

## 4. 차이 요약

| 항목 | Formal | Rapid |
|------|------|------|
| env 경로 | `/opt/api.env` | `/opt/api.env` (동일) |
| 이미지 | ECR pull | ECR pull (동일) |
| 트리거 | deploy.ps1 또는 CI → instance refresh | cron 2분마다 main 변경 시 또는 수동 Deploy |
| 반영 범위 | LT·ASG·새 인스턴스 | 기존 인스턴스에서 컨테이너만 |

**결과물(이미지·env·실행 방식)은 동일.** 트리거와 반영 범위만 다름.

---

## 5. Rapid Deploy 스크립트 동작 확인

- **Status:** crontab + 마지막 배포 정보 조회.
- **Off:** cron 제거.
- **Deploy:** SSM으로 repo 갱신 후 `deploy_api_on_server.sh` 1회 실행. **빌드 없음** → ECR pull + 재시작만으로 **수 분 이내** 완료.
- **On:** 2분마다 main 변경 시 `git reset --hard origin/main` 후 `deploy_api_on_server.sh` 실행 (최신 스크립트 사용).

---

## 6. 권장 사항

- **인프라·안정 반영:** Formal Deploy (`deploy.ps1` 또는 main push로 CI deploy-api-refresh). [FORMAL-DEPLOY.md](FORMAL-DEPLOY.md)
- **개발 중 빠른 반영:** Rapid Deploy ON → 수정 반영 후 **작업 종료 시 OFF**. [RAPID-DEPLOY.md](RAPID-DEPLOY.md), [Rapid-Deploy-사용법.md](Rapid-Deploy-사용법.md)
