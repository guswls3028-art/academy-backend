# API 환경변수 배포 플로우 — 서버 수동 셋팅 없이

**목적**: "도커 배포인데 서버에 뭘 세팅하라"는 상황을 없애고, **설정 출처를 한 곳(SSM)**으로 고정한다.

---

## 1. 왜 "서버에 셋팅하라"가 나오는가

- **이미지**는 ECR에서 오지만, **실행 시 env**는 `docker run --env-file /home/ec2-user/.env` 로 **호스트의 .env**에서 주입된다.
- 따라서 런타임 설정의 실제 출처는 **서버의 .env 파일**이다.
- 이 .env를 **어디서 채우느냐**가 일관되지 않으면, 매번 "서버에서 이거 넣고 저거 넣고"가 반복된다.

---

## 2. 설정의 단일 출처 (SSOT)

| 대상 | SSOT |
|------|------|
| API 서버 (academy-api) | **SSM `/academy/api/env`** |
| 워커 (ASG) | **SSM `/academy/workers/env`** |

- **API에 필요한 전체 변수** (DB_*, REDIS_*, R2_*, LAMBDA_INTERNAL_API_KEY, DJANGO_SETTINGS_MODULE, INTERNAL_API_ALLOW_IPS 등)는 **한 번에** `/academy/api/env` 에 들어 있어야 한다.
- SSM이 **한두 줄만** 있으면, 그걸로 .env를 덮어쓸 때 DB 등이 빠져 500/503 이 난다.

---

## 3. 권장 플로우 (서버 수동 편집 없음)

### 3.1 로컬에서 SSM 갱신

로컬 .env에 DB, Redis, R2, Lambda 내부 키 등 **전부** 들어 있다고 가정:

```powershell
.\scripts\upload_env_to_ssm.ps1
```

- `.env` 전체를 `/academy/workers/env` 와 `/academy/api/env` 에 **덮어써서** 올린다.
- **LAMBDA_INTERNAL_API_KEY만** 넣고 싶을 때는 `add_lambda_internal_key_api.ps1` 를 쓰면 되는데, **SSM get이 실패하면** 이 스크립트는 더 이상 SSM을 한 줄로 덮어쓰지 않고 **exit 1** 한다. 반드시 먼저 `upload_env_to_ssm.ps1` 로 전체를 올린 뒤 사용한다.

### 3.2 API 서버 배포 (EC2)

**방법 A — SSM → .env 덮어쓰기 후 빌드·재시작 (배포 표준)**

EC2에서:

```bash
cd /home/ec2-user/academy
bash scripts/deploy_api_on_server.sh
```

- SSM `/academy/api/env` **전체**를 .env에 덮어쓴 뒤, 필수 키(DB_HOST, R2_*, REDIS_HOST 등) 검사 후 빌드·재시작한다.
- **서버에서 nano .env 할 필요 없음.**

**방법 B — SSM과 기존 .env 병합 후 재시작만**

(이미지 빌드는 하지 않고, env만 반영할 때)

로컬에서:

```powershell
.\scripts\sync_api_env_lambda_internal.ps1
```

- 로컬 .env → SSM 업로드 후, EC2에서 `merge_ssm_into_env.sh /academy/api/env` + `refresh_api_container_env.sh` 로 **병합** 후 컨테이너만 재생성한다.
- 이때 **SSM에 이미 전체 env가 있어야** DB 등이 빠지지 않는다.

---

## 4. DB null / 500 나왔을 때 복구 (한 방)

1. **로컬**에서 전체 .env가 들어 있는지 확인한 뒤:
   ```powershell
   .\scripts\upload_env_to_ssm.ps1
   ```
2. **EC2**에서:
   ```bash
   cd /home/ec2-user/academy
   bash scripts/deploy_api_on_server.sh
   ```
   - 또는 SSM만 .env로 덮어쓴 뒤 재시작만 하려면:
   ```bash
   aws ssm get-parameter --name /academy/api/env --with-decryption --region ap-northeast-2 --query Parameter.Value --output text | sed 's/\t/\n/g' | grep -v '^$' > /home/ec2-user/.env
   bash scripts/refresh_api_container_env.sh
   ```

서버에서 **nano로 DB_* 를 직접 넣을 필요 없음**. SSM에 전체가 있으면 위 한두 번 실행으로 끝난다.

---

## 5. 주의 (재발 방지)

- **add_lambda_internal_key_api.ps1**  
  - SSM get 실패/비어 있으면 **한 줄로 덮어쓰지 않고** 종료한다.  
  - 먼저 `upload_env_to_ssm.ps1` 로 전체 env를 SSM에 넣은 다음 실행할 것.
- **merge_ssm_into_env.sh**  
  - SSM 내용을 먼저 쓰고, 기존 .env에만 있는 키를 보존한다.  
  - SSM에 키가 거의 없으면 결과 .env도 그만큼만 있게 되므로, **API용 merge 전에 SSM에 전체가 들어 있어야** 한다.

---

## 6. 요약

| 하고 싶은 일 | 하는 일 |
|-------------|----------|
| API env 전체 반영 (서버 수동 없이) | 로컬: `upload_env_to_ssm.ps1` → EC2: `deploy_api_on_server.sh` 또는 SSM → .env → `refresh_api_container_env.sh` |
| Lambda 내부 키만 추가/갱신 | SSM에 이미 전체가 있을 때만 `add_lambda_internal_key_api.ps1` |
| DB null / 500 복구 | `upload_env_to_ssm.ps1` 후 EC2에서 `deploy_api_on_server.sh` (또는 SSM → .env → refresh) |

**도커 배포**는 그대로 두고, **설정은 SSM 한 곳**에서만 관리하면 서버에 반복해서 뭘 세팅하라고 할 필요가 없다.
