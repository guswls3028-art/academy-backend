# 정석 배포 vs 원격(레거시) 배포 비교

**작성일:** 2026-03-09

---

## 1. 결론: **같은 배포가 아님**

| 구분 | 정석 배포 | 원격(레거시) 배포 |
|------|-----------|-------------------|
| **진입점** | `pwsh scripts/v1/deploy.ps1 -AwsProfile default` | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Deploy` (또는 On/Off/Status) |
| **이미지 출처** | **ECR** (로컬/CI에서 push한 이미지) | **서버에서 docker build** (Git 레포 기준) |
| **env 파일** | SSM → **/opt/api.env** (Launch Template UserData) | SSM → **/home/ec2-user/.env** (deploy_api_on_server.sh) |
| **Git 레포** | 인스턴스에 **없음** (부팅 시 UserData만 실행) | 인스턴스에 **필요** (/home/ec2-user/academy) |
| **빌드 위치** | 로컬 또는 **GitHub Actions** (빌드 서버 0대) | **EC2 API 인스턴스에서** 빌드 |
| **반영 시점** | deploy.ps1 실행 → LT 갱신 → **instance refresh** → 새 인스턴스 부팅 시 최신 UserData·이미지 | On 시 **2분마다 cron** 또는 수동 Deploy 시 **deploy_api_on_server.sh** 1회 실행 |

---

## 2. 정석 배포 흐름 (deploy.ps1)

1. `scripts/v1/deploy.ps1` 실행 (이미지 빌드 없음, `-SkipBuild` 기본).
2. SSM `/academy/api/env` 갱신, Launch Template UserData 갱신 (SSM → `/opt/api.env`, ECR 이미지 URI로 `docker pull` + `docker run --env-file /opt/api.env`).
3. API ASG **instance refresh** 시작 → 새 인스턴스가 뜨면 UserData 실행:
   - Docker 설치, ECR 로그인, `docker pull <ECR URI>`, SSM으로 `/opt/api.env` 생성, `docker run -d --env-file /opt/api.env <ECR URI>`.
4. 기존 인스턴스는 refresh 정책에 따라 순차 종료. **인스턴스에는 Git 레포가 없음.**

**관련 파일:** `scripts/v1/deploy.ps1`, `scripts/v1/resources/api.ps1` (Get-ApiLaunchTemplateUserData).

---

## 3. 원격(레거시) 배포 흐름 (api-auto-deploy-remote.ps1)

1. **Action Status:** SSM으로 해당 인스턴스에 `crontab -l` 실행 → crontab 상태만 확인.
2. **Action Off:** SSM으로 `git fetch` + `git reset --hard origin/main` 후 `scripts/auto_deploy_cron_off.sh` 실행 → crontab에서 deploy 관련 라인 제거.
3. **Action On:** SSM으로 (레포 없으면 clone) `git fetch` + `git reset --hard origin/main` 후 `scripts/auto_deploy_cron_on.sh` 실행 → 2분마다 main 변경 시 `deploy_api_on_server.sh` 실행하는 cron 등록.
4. **Action Deploy:** SSM으로 동일하게 repo 준비 후 `scripts/deploy_api_on_server.sh` **1회** 실행.

**deploy_api_on_server.sh (서버 내부):**

- SSM `/academy/api/env` 조회 → **/home/ec2-user/.env** 에 저장 (기본값 `ENV_FILE`).
- `cd /home/ec2-user/academy`, `git pull origin main`.
- `docker image prune -f`.
- **docker build** `-f docker/Dockerfile.base` → academy-base:latest.
- **docker build** `-f docker/api/Dockerfile` → academy-api:latest.
- `docker image prune -f`.
- `docker stop/rm academy-api`, `docker run --rm --env-file "$ENV_FILE" academy-api:latest python manage.py migrate`.
- `docker run -d --name academy-api --env-file "$ENV_FILE" -p 8000:8000 academy-api:latest`.

**관련 파일:** `scripts/v1/api-auto-deploy-remote.ps1`, `scripts/deploy_api_on_server.sh`, `scripts/auto_deploy_cron_on.sh`, `scripts/auto_deploy_cron_off.sh`.

---

## 4. 차이 요약

| 항목 | 정석 | 원격(레거시) |
|------|------|--------------|
| env 경로 | `/opt/api.env` | `/home/ec2-user/.env` |
| 이미지 | ECR pull (동일 이미지 공유) | 서버에서 빌드 (academy-api:latest) |
| 인스턴스에 Git | 없음 | 있음 (/home/ec2-user/academy) |
| 배포 트리거 | deploy.ps1 → instance refresh | cron 2분마다 또는 수동 Deploy |

**같은 배포가 아니며**, 정석은 ECR+UserData+instance refresh, 레거시는 Git+서버 빌드+env 파일 경로(.env) 사용.

---

## 5. 원격 스크립트 동작 확인 (실제 실행 결과)

- **Status:** 실행됨. 인스턴스 `i-0b007fab7c0528a7b` 에서 crontab 상태 조회 성공.
- **Off:** 실행됨. 이후 Status 시 `No crontab` 확인됨 (cron 제거 정상).
- **On:** 실행함 (git fetch + cron 등록). 완료까지 대기 시간 발생 가능.
- **Deploy:** 수동 1회 배포. 서버에서 `deploy_api_on_server.sh` 실행 시 **git pull + docker build 2회 + migrate + docker run** 으로 **10~20분** 소요될 수 있음. 필요 시 `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Deploy -AwsProfile default` 로 직접 실행 권장.

---

## 6. 권장 사항

- **일상 배포:** 정석 배포만 사용 (`deploy.ps1`). ECR에 이미지 push 후 deploy.ps1 실행.
- **원격 스크립트:** 레거시/긴급 수동 배포용. On 시 2분마다 main 변경 시 서버 빌드가 돌아가므로, 정석 배포를 쓰면 **Off** 로 두고 필요할 때만 **Deploy** 1회 실행하는 것을 권장.
- **env 경로 통일:** 레거시에서도 `/opt/api.env`를 쓰려면 `deploy_api_on_server.sh` 의 `ENV_FILE` 기본값을 `/opt/api.env`로 변경하고, SSM으로 쓰는 로직을 UserData와 동일하게 두면 정석과 동일 경로 사용 가능 (선택).
