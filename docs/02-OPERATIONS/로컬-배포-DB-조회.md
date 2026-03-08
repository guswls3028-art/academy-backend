# 로컬 DB vs 배포 DB — 사실 기준 조회

## 결론 (최종 조회 기준)

| 구분 | DB_HOST | DB_NAME | 출처 |
|------|---------|---------|------|
| **로컬** (manage.py 기준) | `academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com` | `postgres` | 루트 `.env` → `.env.local` (덮어쓰기) |
| **배포** (API 서버) | `academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com` | `postgres` | SSM `/academy/api/env` → 서버 `/home/ec2-user/.env` |

**즉, 현재 설정상 로컬과 배포는 동일한 RDS 인스턴스(academy-db / postgres)를 가리킵니다.**  
“두 개”는 DB 인스턴스가 둘이라는 뜻이 아니라, **설정을 주입하는 경로가 두 가지**라는 의미입니다.

---

## 왜 설정 경로가 두 개인가

- **로컬**: `manage.py`가 `load_dotenv(.env)` 후 `load_dotenv(.env.local)`로 **로컬 디스크의 .env 파일**을 읽음.  
  → 로컬에서 `python manage.py` 실행 시 사용하는 DB = `.env` + `.env.local`에 적힌 `DB_HOST` / `DB_NAME` 등.

- **배포**: API EC2에서는 **SSM Parameter Store `/academy/api/env`**가 단일 소스.  
  `scripts/deploy_api_on_server.sh`가 SSM 값을 가져와 `/home/ec2-user/.env`를 만들고, 그 파일로 컨테이너를 띄움.  
  → 배포 API가 사용하는 DB = SSM `/academy/api/env` 안의 `DB_HOST` / `DB_NAME` 등.

같은 RDS를 쓰려면:

- 로컬에서 배포와 동일한 DB를 쓰고 싶을 때:  
  `pwsh scripts/v1/sync-local-env-from-ssm.ps1 -AwsProfile default` 로 SSM의 DB/Redis 값을 로컬 `.env`에 반영.
- 로컬만 다른 DB(예: localhost PostgreSQL)를 쓰고 싶을 때:  
  `.env.local`에 `DB_HOST=127.0.0.1`, `DB_NAME=...` 등 로컬 전용 값을 두면 `.env`를 덮어씀.

---

## 직접 다시 조회하는 방법

### 로컬이 지금 쓰는 DB

```powershell
cd C:\academy
python -c "
from pathlib import Path
from dotenv import load_dotenv
import os
BASE = Path('.').resolve()
load_dotenv(BASE / '.env')
load_dotenv(BASE / '.env.local')
print('DB_HOST=', os.getenv('DB_HOST'))
print('DB_NAME=', os.getenv('DB_NAME'))
"
```

### 배포(SSM)가 가리키는 DB

```powershell
cd C:\academy
pwsh -File scripts/v1/run-with-env.ps1 -- aws ssm get-parameter --name "/academy/api/env" --with-decryption --region ap-northeast-2 --query "Parameter.Value" --output text
```

출력된 JSON에서 `DB_HOST`, `DB_NAME` 확인.

---

## 참고: 코드상 출처

- 로컬 env 로드: `manage.py` 10–13행 (`.env` → `.env.local`).
- 배포 env 생성: `scripts/deploy_api_on_server.sh` (SSM `/academy/api/env` → `/home/ec2-user/.env`).
- SSOT 파라미터 이름: `docs/00-SSOT/v1/params.yaml` → `ssm.apiEnv: /academy/api/env`.
- 로컬에 배포 DB 반영: `scripts/v1/sync-local-env-from-ssm.ps1`, `docs/02-OPERATIONS/배포.md` §11.
