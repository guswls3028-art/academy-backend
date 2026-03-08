# Rapid Deploy 사용법

**목적:** 개발 중 백엔드 코드를 자주 수정할 때, push된 최신 코드가 **API 서버에만** 빠르게 반영되도록 하는 체계.  
**정식 배포(deploy.ps1)와 별개**이며, ASG instance refresh는 사용하지 않음.

**상세:** [RAPID-DEPLOY.md](RAPID-DEPLOY.md) · **2트랙 개요:** [DEPLOYMENT-MODES.md](DEPLOYMENT-MODES.md)

---

## 1. Rapid Deploy ON 명령

```powershell
pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On -AwsProfile default
```

- 서버에 2분마다 **main 변경 감지** cron 등록.
- 변경이 있으면 **ECR pull + API 컨테이너만 재시작** (인스턴스 교체 없음).

---

## 2. Rapid Deploy OFF 명령

```powershell
pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default
```

- 서버에서 자동 감지/자동 반영 **중단**. 수동 배포만 가능.

---

## 3. 현재 상태 확인 명령

```powershell
pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default
```

- crontab 등록 여부 + **마지막 배포 정보**(deployed_at, image, container_id) 출력.

---

## 4. 최근 반영 버전 확인 방법

- **원격(Status):**  
  `pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default`  
  → 출력에 `deployed_at`, `image`, `container_id` 포함.

- **서버에서 직접:**  
  SSM Session Manager 등으로 API 인스턴스 접속 후:
  ```bash
  cat /home/ec2-user/.academy-rapid-deploy-last
  ```
  또는
  ```bash
  docker inspect academy-api --format '{{.RepoDigests}}'
  ```

- **CI 빌드와 비교:**  
  `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api digest와 서버의 RepoDigests가 일치하면 최신 이미지 반영된 상태.

---

## 5. Rapid Deploy 사용 시 주의사항

- **전제:** main에 push되면 **GitHub Actions**가 academy-api 이미지를 ECR에 푸시해야 함. CI가 실패하면 서버가 pull해도 예전 이미지.
- **반영 지연:** push → CI 빌드(수 분) → 그 후 **최대 2분** 안에 서버 cron이 main 변경 감지 후 배포. 즉, “push 직후 2분”이 아니라 “**CI가 이미지 푸시를 끝낸 뒤** 최대 2분”에 반영됨.
- **멀티테넌트:** env는 SSM `/academy/api/env` → `/opt/api.env` 로만 로드. 테넌트 격리·폴백 정책은 정식 배포와 동일하며, Rapid Deploy가 이를 약화하지 않음.
- **로컬 자동 push:** 저장소에는 포함되지 않음. 사용자가 원하면 별도 도구로 “로컬 자동 git push” ON/OFF 설정.

---

**실패 시 로그 확인:**

- **서버 자동배포 로그:** `/home/ec2-user/auto_deploy.log` (cron이 2분마다 실행한 내용·에러가 여기로 감). 서버 접속 후 `tail -f /home/ec2-user/auto_deploy.log` 로 실시간 확인 가능.
- **SSM 명령 실패:** `-Action On/Off/Deploy` 실행 시 터미널에 stderr가 출력됨. 실패 시 해당 인스턴스에서 수동으로 `bash scripts/deploy_api_on_server.sh` 실행해 에러 메시지 확인.
- **health check 경고:** deploy_api_on_server.sh 실행 후 `/healthz` 실패 시 스크립트가 WARN 출력. 컨테이너는 기동된 상태이므로 `docker logs academy-api` 로 원인 확인.

---

## 6. Formal Deploy가 필요한 경우

- **인프라 변경** 반영(Launch Template, ASG, ALB, SSM 등).
- **안정 반영**이 필요할 때(예: 출시 전/후).
- **Rapid Deploy를 OFF로 둔 상태**에서 수동으로 한 번만 배포하고 싶을 때는 `-Action Deploy` 또는 정식 배포 사용.

**정식 배포 실행:**
```powershell
pwsh scripts/v1/deploy.ps1 -AwsProfile default
```

---

## 7. 관련 문서 업데이트 내용

| 문서 | 내용 |
|------|------|
| `docs/02-OPERATIONS/DEPLOYMENT-MODES.md` | 배포 2트랙(Formal vs Rapid) 개요·비교표·언제 무엇을 쓸지. |
| `docs/02-OPERATIONS/FORMAL-DEPLOY.md` | Formal Deploy 상세. |
| `docs/02-OPERATIONS/RAPID-DEPLOY.md` | Rapid Deploy 상세. |
| `docs/02-OPERATIONS/Rapid-Deploy-ON-OFF-분석보고서.md` | 현재 구조, 끊기는 지점, 구현 가능 여부, 수정 항목, 권장 구조. |
| `docs/02-OPERATIONS/Rapid-Deploy-사용법.md` | 본 문서. ON/OFF/Status/최근 반영/주의사항/Formal Deploy 구분. |
| `scripts/v1/api-auto-deploy-remote.ps1` | 주석에 Rapid Deploy 명칭, Status 시 마지막 배포 정보 출력. |
| `scripts/deploy_api_on_server.sh` | Rapid Deploy 주석, health check, `.academy-rapid-deploy-last` 기록. |

---

## 8. 운영 흐름 요약

| 단계 | 동작 |
|------|------|
| **개발 시작** | (선택) 로컬 자동 git push ON. **Rapid Deploy ON** (`-Action On`). |
| **개발 중** | 코드 수정 → push → CI가 ECR에 이미지 푸시 → 서버가 2분 이내 main 변경 감지 → API 컨테이너만 pull/restart. |
| **작업 종료** | 로컬 자동 push OFF. **Rapid Deploy OFF** (`-Action Off`). |
| **필요 시** | Formal 배포는 `deploy.ps1`로 별도 실행. |
