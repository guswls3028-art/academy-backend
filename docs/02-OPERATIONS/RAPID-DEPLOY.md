# Rapid Deploy (빠른 개발 반영 배포)

**목적:** 개발 중 백엔드 코드를 자주 수정할 때, push된 최신 코드가 **API 서버에만** 빠르게 반영되도록 하는 체계.  
**정식 배포(deploy.ps1)와 별개**이며, **ASG instance refresh는 사용하지 않는다.** 컨테이너만 pull/restart한다.

---

## 1. 목적

- 개발 중 **빠른 코드 반영**, 잦은 수정 대응.
- **API 컨테이너만** ECR pull 후 재시작 (기존 인스턴스 유지).
- **2분 주기**로 main 변경 감지 후 자동 반영 (ON 상태일 때).

---

## 2. 실행 방식

### 2.1 ON / OFF / Status / Deploy

| 동작 | 명령 |
|------|------|
| **Rapid Deploy ON** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default` |
| **Rapid Deploy OFF** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default` |
| **상태 확인** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default` |
| **수동 1회 배포** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Deploy -AwsProfile default` |

### 2.2 ON 시 동작

- API ASG 인스턴스에 SSM Send-Command로:
  1. 레포 없으면 clone, `git fetch origin main && git reset --hard origin/main`
  2. `bash scripts/auto_deploy_cron_on.sh` 실행 → **2분마다** 실행되는 cron 등록.
- Cron 내용: `cd $REPO_DIR && git fetch origin main`, `HEAD` vs `origin/main` 비교, **다르면** `git reset --hard origin/main` 후 `bash scripts/deploy_api_on_server.sh`.

### 2.3 감지 주기·감지 기준

- **주기:** 2분 (`*/2 * * * *`).
- **감지 기준:** `git rev-parse HEAD` vs `git rev-parse origin/main` 불일치 시 “변경 있음”으로 보고 배포 실행.
- **최신 이미지 반영:** main이 갱신된 뒤 **CI가 ECR에 academy-api:latest를 푸시한 후**에야 서버가 pull하는 이미지가 최신이 됨. 즉, “push → CI 빌드 완료 → (최대 2분 이내) cron이 main 변경 감지 → deploy_api_on_server.sh” 순서.

### 2.4 서버에서 하는 일 (deploy_api_on_server.sh)

1. SSM `/academy/api/env` 조회 → **/opt/api.env**에 저장 (정식 배포와 동일 경로).
2. ECR 로그인, `docker pull` academy-api:latest.
3. `docker stop academy-api`, `docker rm academy-api`, `docker run -d --restart unless-stopped --name academy-api -p 8000:8000 --env-file /opt/api.env <ECR_URI>`.
4. sleep 3 후 **health check:** `curl -sf --max-time 10 http://localhost:8000/healthz`. 실패 시 WARN만 출력(컨테이너는 기동된 상태).
5. 마지막 배포 정보 기록: `/home/ec2-user/.academy-rapid-deploy-last` (deployed_at, image, container_id).
6. `docker image prune -f`.

**관련 파일:** `scripts/deploy_api_on_server.sh`, `scripts/auto_deploy_cron_on.sh`, `scripts/auto_deploy_cron_off.sh`.

---

## 3. 특징

- **빠름.** 인프라 재구성·instance refresh 없음.
- **개발 생산성용.** Formal Deploy를 대체하지 않음.
- **ON/OFF 명시적 제어.** 작업 종료 시 OFF 필수.

---

## 4. 언제 써야 하는지

- **개발 중** API 코드만 자주 수정하고, instance refresh 대기 없이 **컨테이너만** 빠르게 반영하고 싶을 때.
- 인프라/SSM 구조는 그대로 두고 **이미지만** 최신으로 갈아끼우면 될 때.

---

## 5. 언제 쓰면 안 되는지

- **Launch Template, UserData, ASG, ALB** 등 인프라 변경을 반영해야 할 때 → Formal Deploy 사용.
- **SSM `/academy/api/env`만** 바꾼 경우: Rapid Deploy의 deploy_api_on_server.sh가 SSM→/opt/api.env를 하므로, 다음 2분 주기 또는 `-Action Deploy` 1회로 반영 가능. 단, “인프라까지 정석 반영”이 목적이면 Formal 사용.

---

## 6. 작업 종료 후 왜 OFF 해야 하는지

- Rapid Deploy ON 상태로 두면 **main에 올라가는 모든 변경**(다른 사람 push 포함)이 최대 2분 안에 API 서버에 자동 반영됨.
- 의도치 않은 반영·불안정 상태 유지를 막기 위해, **작업 종료 시 반드시** `-Action Off` 실행. 로컬 자동 git push도 함께 끄는 것을 권장.

---

## 7. 최근 반영 버전 확인

- **원격:** `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default` → crontab + `cat /home/ec2-user/.academy-rapid-deploy-last` 출력.
- **서버에서 직접:**  
  `cat /home/ec2-user/.academy-rapid-deploy-last`  
  `docker inspect academy-api --format '{{.RepoDigests}}'`
- **CI digest와 비교:** `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api digest와 서버 RepoDigests 일치 여부.

---

## 8. 실패 시 로그 확인

- **서버 자동배포 로그:** `/home/ec2-user/auto_deploy.log`. `tail -f /home/ec2-user/auto_deploy.log` 로 실시간 확인.
- **SSM 명령 실패:** On/Off/Deploy 실행 시 터미널 stderr. 실패 시 해당 인스턴스에서 수동으로 `bash scripts/deploy_api_on_server.sh` 실행해 에러 확인.
- **health check 경고:** deploy_api_on_server.sh에서 `/healthz` 실패 시 WARN 출력. `docker logs academy-api`로 원인 확인.

---

## 9. 멀티테넌트 관련

- env는 **SSM `/academy/api/env` → `/opt/api.env`** 만 사용. 정식 배포와 동일. **Rapid Deploy 중에도 tenant isolation 완화 금지.** tenant fallback·default tenant·tenant 없는 query 금지.

---

## 10. 관련 문서

- `docs/02-OPERATIONS/DEPLOYMENT-MODES.md` — Formal vs Rapid 비교
- `docs/02-OPERATIONS/Rapid-Deploy-사용법.md` — 요약·명령 치트시트
- `docs/02-OPERATIONS/정석-배포-vs-원격-배포-비교.md`
