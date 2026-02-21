# STRICT — HakwonPlus API ENV 복구 (NO ASSUMPTIONS)

**증상**: `DATABASES` HOST/NAME null, backlog-count 500, Lambda metric 미발행, ASG 스케일 중단.

**원칙**: SSM → .env 플로우만 사용. 서버에서 `nano` 등 수동 편집 금지. 리포 스크립트만 사용.

---

## 1. 로컬 — SSM에 전체 env 있는지 검증

```powershell
.\scripts\verify_ssm_api_env.ps1
```

- **exit 0**: SSM에 DB_HOST, DB_NAME, REDIS_*, R2_* 등 필수 키 있음 → 2단계로.
- **exit 1**: SSM 손상 또는 키 누락 → 아래 실행 후 다시 1단계.

```powershell
.\scripts\upload_env_to_ssm.ps1
```

(전체 `.env`가 현재 디렉터리 또는 리포 루트에 있어야 함.)

---

## 2. EC2 — API 재배포 (SSM → .env → 빌드·재시작)

```bash
cd /home/ec2-user/academy
bash scripts/deploy_api_on_server.sh
```

- SSM `/academy/api/env` 전체를 `/home/ec2-user/.env`에 덮어쓴 뒤 필수 키 검사, 빌드, `docker run --env-file` 실행.
- 실패 시 스크립트가 `ERROR: Required env missing...` 출력. → 1단계에서 `upload_env_to_ssm.ps1` 재실행 후 SSM 확인.

---

## 3. EC2 — 재생성 후 검증

```bash
bash scripts/verify_api_after_deploy.sh
```

- `DATABASES` default.HOST / NAME 이 null 이 아닌지 확인.
- `GET /api/v1/internal/video/backlog-count/` (X-Internal-Key) → 200 확인.
- 둘 다 통과할 때까지 **완료 아님**. 200 안 나오면 1·2 다시 점검.

---

## 4. 완료 조건

- `settings.DATABASES['default']['HOST']` != null
- `backlog-count` HTTP 200

이후 Lambda invoke → CloudWatch BacklogCount datapoint 확인.

---

## 사용 스크립트만 (가정 없음)

| 단계 | 스크립트 |
|------|----------|
| SSM 검증 | `scripts/verify_ssm_api_env.ps1` |
| SSM 복구 | `scripts/upload_env_to_ssm.ps1` |
| API 재배포 | `scripts/deploy_api_on_server.sh` |
| 배포 후 검증 | `scripts/verify_api_after_deploy.sh` |

Django 쪽 일반적 수정이나 서버에서 `.env` 수동 편집은 하지 않음.
