# Rapid Deploy ON/OFF 체계 분석 보고서

**작성일:** 2026-03-09  
**기준:** academy-backend 저장소 실제 코드·스크립트

---

## 1. 현재 실제 구조

### 1.1 자동 git push 구조

- **저장소 내 구현:** 없음. academy-backend 리포에는 “로컬 자동 git push” 스크립트나 워크플로가 **포함되어 있지 않음**.
- **운영 가정:** 사용자가 별도 도구(예: 파일 감시 후 `git add` / `git commit` / `git push`, 또는 Cursor/IDE 연동)로 main에 push한다고 가정. Rapid Deploy는 **“main이 갱신되면”** 그 다음 단계만 담당.

### 1.2 GitHub Actions 이미지 빌드/push 구조

| 항목 | 내용 |
|------|------|
| **파일** | `.github/workflows/v1-build-and-push-latest.yml` |
| **트리거** | `push` to `main`, `workflow_dispatch` |
| **동작** | academy-base → academy-api 등 5개 이미지 **linux/arm64** 빌드 후 ECR에 **`academy-api:latest`** 등 **latest** 푸시 |
| **추가** | 푸시 후 `docs/00-SSOT/v1/reports/ci-build.latest.md`에 digest 기록하고 해당 파일만 커밋·푸시 |
| **결과** | main에 push가 일어나면 CI가 **새 이미지를 ECR에 푸시**함. (빌드 소요 시간만큼 지연.) |

### 1.3 API 자동배포 스크립트 구조

| 구성요소 | 역할 |
|----------|------|
| **api-auto-deploy-remote.ps1** | SSM으로 API ASG 인스턴스에 명령 전달. **On** / **Off** / **Status** / **Deploy** 네 가지 액션. |
| **On** | 서버에 repo 없으면 clone, `git fetch origin main && git reset --hard origin/main` 실행 후 `scripts/auto_deploy_cron_on.sh` 실행 → cron 등록 |
| **Off** | `git fetch` + `git reset --hard origin/main` 후 `scripts/auto_deploy_cron_off.sh` 실행 → cron에서 deploy 관련 라인 제거 |
| **Status** | `crontab -l` 로 cron 등록 여부 확인 |
| **Deploy** | repo 준비 후 `scripts/deploy_api_on_server.sh` **1회** 실행 (수동 1회 배포) |
| **auto_deploy_cron_on.sh** | 2분마다 실행되는 cron 한 줄 등록. `git fetch origin main` → `HEAD` vs `origin/main` 비교 → 다르면 `git reset --hard origin/main` 후 `scripts/deploy_api_on_server.sh` 실행. 로그: `$LOG_FILE` (기본 `/home/ec2-user/auto_deploy.log`) |
| **auto_deploy_cron_off.sh** | crontab에서 `deploy_api_on_server.sh` 포함 라인 제거 |
| **deploy_api_on_server.sh** | SSM `/academy/api/env` → `/opt/api.env`, ECR 로그인 → `docker pull academy-api:latest` → `docker stop/rm academy-api` → `docker run -d ... --env-file /opt/api.env ... academy-api:latest` → `docker image prune -f`. **ASG instance refresh 없음.** 컨테이너만 교체. |

### 1.4 현재 ON/OFF 동작 방식

- **Rapid Deploy ON:**  
  `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default`  
  → 서버에 2분마다 main 변경 감지 → 변경 시 `deploy_api_on_server.sh` 실행 → ECR pull + API 컨테이너만 재시작.
- **Rapid Deploy OFF:**  
  `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default`  
  → cron 제거, 자동 감지/반영 중단. 수동 배포만 가능(Deploy 액션 또는 서버에서 직접 스크립트 실행).
- **멀티테넌트:**  
  env는 SSM `/academy/api/env` → `/opt/api.env` 로만 로드. 테넌트 격리/폴백 로직은 이 스크립트에 없으며, 기존 정식 배포와 동일한 env 경로·방식을 사용함.

---

## 2. 현재 끊기는 지점

### 2.1 push만으로 서버 반영이 “완전히” 되려면

- **서버 반영 경로:**  
  main 갱신 → **CI가 ECR에 academy-api:latest 푸시** → 서버 cron이 main 변경 감지 → `deploy_api_on_server.sh` 실행 → `docker pull` + 재시작.
- **끊기는 지점:**  
  1. **CI가 main push에 의해 실행되어야 함.** (main push → workflow 실행 → ECR 푸시.)  
  2. **서버가 “main 변경”을 감지하는 시점**은 **git의 origin/main** 기준. 즉, CI가 `ci-build.latest.md` 커밋을 push한 뒤에야 서버가 “변경 있음”으로 볼 수 있음. (또는 사용자 커밋만으로 main이 바뀐 시점에 한 번 감지되지만, 그때는 아직 CI가 이미지 푸시를 끝내지 않았을 수 있음.)
- **실제 타임라인:**  
  - T+0: 사용자(또는 로컬 자동 push)가 main에 push  
  - T+0~약 5–10분: CI 빌드·ECR 푸시  
  - T+빌드 완료 시: CI가 ci-build.latest.md 커밋 푸시(선택)  
  - T+그 이후 최대 2분: 서버 cron이 main 변경 감지 → deploy 실행 → **그 시점의** ECR `academy-api:latest` pull  
- **정리:**  
  - “push만으로 서버 반영”은 **가능**하지만, **CI 빌드가 끝나고(그리고 필요 시 CI 커밋이 main에 반영된 뒤) 최대 2분 이내**에 반영됨.  
  - CI가 실패하거나 main에 푸시되지 않으면, 서버가 아무리 cron을 돌려도 **새 이미지**는 ECR에 없으므로 반영되지 않음.

### 2.2 필요한 전제

1. **main에 코드가 push됨** (로컬 자동 push 또는 수동 push).  
2. **GitHub Actions**가 해당 push로 실행되어 **academy-api:latest**를 ECR에 푸시함.  
3. **Rapid Deploy가 ON**되어 있어 서버에 2분 주기 cron이 등록되어 있음.  
4. API 인스턴스가 **SSM 연결 가능**하고, **ECR pull** 및 **/opt/api.env** 사용 권한이 있음.

---

## 3. 원하는 운영 방식 구현 가능 여부

- **구현 가능.**  
  - “개발 중: 로컬 자동 git push ON + API Rapid Deploy ON” → 이미 **api-auto-deploy-remote.ps1 -Action On**으로 가능.  
  - “작업 종료: 자동 push OFF + API Rapid Deploy OFF” → **api-auto-deploy-remote.ps1 -Action Off**로 서버 쪽 자동배포만 OFF 가능. 로컬 자동 push ON/OFF는 저장소 밖 도구 설정 이슈.  
- **보강할 부분:**  
  - “Rapid Deploy”라는 이름과 사용 시나리오를 문서·주석으로 명확히 하고,  
  - ON/OFF/Status 명령을 “Rapid Deploy on/off/status”로 정리하며,  
  - 최근 반영 버전 확인·실패 시 로그 확인 방법·health check 방식을 문서와 필요 시 스크립트에 보강.

---

## 4. 필요한 수정 항목

| 구분 | 항목 | 내용 |
|------|------|------|
| **스크립트** | deploy_api_on_server.sh | (선택) 재시작 후 간단 health check, 마지막 배포 시각/rev 기록 파일 생성 → “최근 반영 버전” 확인용 |
| **스크립트** | api-auto-deploy-remote.ps1 | 주석/도움말에 “Rapid Deploy” 명시, 출력 메시지에 Rapid Deploy ON/OFF 구분 |
| **문서** | Rapid Deploy 전용 문서 | 운영 방식, ON/OFF/Status/Deploy 명령, 전제 조건, Formal Deploy와 차이, 주의사항, 로그·버전 확인 방법 |
| **운영 규칙** | .cursorrules 또는 운영 가이드 | Rapid Deploy는 “개발 중 빠른 반영용”, Formal Deploy(deploy.ps1)는 “안정 반영/인프라 포함용”으로 역할 구분 명시 |

---

## 5. 권장 최종 구조

### 5.1 Rapid Deploy ON

- **실행:** `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default`  
- **동작:** 서버에 repo 준비 후 2분마다 `git fetch origin main` → HEAD ≠ origin/main 이면 `git reset --hard origin/main` 후 `deploy_api_on_server.sh` 실행 → ECR pull + API 컨테이너만 재시작.  
- **ASG instance refresh:** 사용하지 않음. 컨테이너만 교체.

### 5.2 Rapid Deploy OFF

- **실행:** `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default`  
- **동작:** 서버에서 cron 제거. 자동 감지·자동 반영 중단. 수동 배포만 가능.

### 5.3 Health check

- **현재:** deploy_api_on_server.sh는 재시작 후 별도 health check 없음.  
- **권장:** (선택) 재시작 후 `curl -sf http://localhost:8000/healthz` 등으로 한 번 확인하고, 실패 시 로그에 기록. 필수는 아니므로 “최소 health check” 수준으로 문서화 가능.

### 5.4 실패 시 확인 방법

- **서버 로그:** `/home/ec2-user/auto_deploy.log` (cron이 표준출력/에러를 여기로 보냄).  
- **SSM:** 실패한 SSM 명령은 api-auto-deploy-remote.ps1 실행 시 stderr 출력으로 확인.  
- **직접 확인:** SSM Session Manager로 인스턴스 접속 후 `bash scripts/deploy_api_on_server.sh` 수동 실행해 에러 메시지 확인.

---

## 6. 사용 방법 (실제로 쓸 명령)

| 목적 | 명령 |
|------|------|
| **Rapid Deploy ON** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default` |
| **Rapid Deploy OFF** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default` |
| **현재 상태 확인** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default` |
| **수동 1회 배포** | `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Deploy -AwsProfile default` |

이제 위 내용을 반영해 스크립트·문서를 보강하고, “Rapid Deploy” 전용 문서에 최종 사용 방법·주의사항·Formal Deploy와의 차이를 정리하겠습니다.

---

## 7. 구현 반영 사항 (2026-03-09)

- **deploy_api_on_server.sh:** Rapid Deploy 주석, 재시작 후 `/healthz` health check, 마지막 배포 정보를 `/home/ec2-user/.academy-rapid-deploy-last`에 기록.
- **api-auto-deploy-remote.ps1:** Rapid Deploy ON/OFF/Status 라벨 및 요약 출력, Status 시 마지막 배포 정보 출력.
- **문서:** `docs/02-OPERATIONS/Rapid-Deploy-사용법.md` 에 최종 명령, 최근 반영 확인, 주의사항, Formal Deploy 구분 정리.
