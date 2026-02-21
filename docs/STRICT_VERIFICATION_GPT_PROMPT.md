# GPT 프롬프트 검증 — 내 코드 기준 (STRICT, NO ASSUMPTIONS)

**질문**: GPT가 준 "STRICT INVESTIGATION MODE" 복구 절차가 이 프로젝트 구조와 맞는가?

**결론**: **맞음.** 아래는 코드/경로 기준 검증 결과. 단, 환경변수 이름만 프로젝트는 **DB_*** 를 쓰고 **RDS_*** 는 사용하지 않음.

---

## 1. URL 및 엔드포인트

| GPT 프롬프트 | 실제 코드 |
|-------------|-----------|
| `/api/v1/internal/video/backlog-count/` | `apps/api/v1/urls.py` L100: `"internal/video/backlog-count/"` → VideoBacklogCountView |

**일치.**

---

## 2. DATABASES / null 원인

| GPT 프롬프트 | 실제 코드 |
|-------------|-----------|
| `settings.DATABASES` 에 NAME, USER, HOST null | `apps/api/config/settings/base.py` L185-191: `os.getenv("DB_NAME")`, `os.getenv("DB_HOST")` 등 — env 없으면 null |

**일치.** 컨테이너에 DB_* 가 안 들어가면 null.

---

## 3. SSM 덮어쓰기 원인

| GPT 프롬프트 | 실제 코드 (수정 후) |
|-------------|---------------------|
| add_lambda_internal_key_api.ps1 가 SSM get 실패 시 SSM을 한 줄로 덮어씀 | `scripts/add_lambda_internal_key_api.ps1` L27-31: SSM get 실패/비어 있으면 **덮어쓰지 않고 exit 1** (이미 패치됨) |

**과거 동작**: get 실패 시 `$current = ""` → put 시 한 줄만 들어가 SSM 손상 가능. **현재**: 그 경로 제거됨.

---

## 4. 환경변수 이름 (GPT vs 프로젝트)

| GPT 프롬프트 | 프로젝트 실제 |
|-------------|----------------|
| DB_*, RDS_*, REDIS_*, R2_* | **DB_*** (base.py L188-191), **REDIS_*** (libs/redis), **R2_*** (base.py 등). **RDS_*** 는 코드에서 사용 안 함. |

**수정**: "DB_*, RDS_*" → 이 프로젝트는 **DB_*** 만 사용. RDS_* 검사는 불필요.

---

## 5. 배포 스크립트

| GPT 프롬프트 | 실제 코드 |
|-------------|-----------|
| `bash scripts/deploy_api_on_server.sh` | `scripts/deploy_api_on_server.sh` 존재. SSM `/academy/api/env` 전체 → `.env` 덮어쓰기, REQUIRED_KEYS(DB_HOST, R2_*, REDIS_HOST) 검사 후 빌드·`docker run --env-file` |

**일치.**

---

## 6. "DB-backed tenant resolution"

| GPT 프롬프트 | 실제 코드 |
|-------------|-----------|
| backlog API 도 DB 기반 tenant resolution 때문에 DB 필요 | `apps/core/tenant/resolver.py` L98-122: `resolve_tenant_from_request()` 가 **_resolve_tenant_from_host(host)** 를 먼저 호출하고, 그 안에서 `qs.count()` (L45) 로 DB 접근. `/api/v1/internal/` 는 `TENANT_BYPASS_PATH_PREFIXES` 에 있어 bypass 이지만, **bypass 판단 전에** 이미 `_resolve_tenant_from_host()` 가 실행됨. DB 연결 실패 시 그 단계에서 500. |

**일치.** backlog-count 뷰 자체는 Redis만 쓰지만, **미들웨어 이전**에 tenant resolver 가 DB를 타므로 DB 없으면 500.

---

## 7. 검증 명령어 (컨테이너 내부)

| GPT 프롬프트 | 비고 |
|-------------|------|
| `docker exec academy-api python -c "from django.conf import settings; print(settings.DATABASES['default'])"` | TTY 불필요하므로 `docker exec academy-api` 만 사용 (이미지에 따라 `python3` 일 수 있음) |
| `docker exec academy-api python -c "import os,requests; print(requests.get(..., headers={'X-Internal-Key':...}).status_code)"` | 컨테이너에 `requests` 가 있어야 함. 없으면 `curl` 또는 `urllib` 로 대체 가능. |

**일치.** 단, 이미지에 `requests` 없을 수 있음 — 그때는 `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/internal/video/backlog-count/ -H "X-Internal-Key: $LAMBDA_INTERNAL_API_KEY"` (EC2에서 .env 로드 후) 사용.

---

## 8. Lambda / CloudWatch 단계

| GPT 프롬프트 | 비고 |
|-------------|------|
| backlog-count 200 확인 **후** Lambda invoke, BacklogCount 메트릭 확인 | Lambda는 VIDEO_BACKLOG_FETCH_URL 등으로 해당 URL 호출. DB/API 200 이 아니면 metric publish 안 됨. |

**일치.**

---

## 9. 정리

- GPT가 준 **조사·복구 순서와 원인 설명은 이 레포 구조와 일치**함.
- **차이점**: 환경변수는 **DB_*** 사용, **RDS_*** 는 사용하지 않음** — SSM/호스트 .env 확인 시 DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, REDIS_*, R2_* 위주로 보면 됨.
- **절차 그대로 진행해도 됨.** (1) SSM 내용 확인 → (2) 필요 시 로컬 전체 .env 로 `upload_env_to_ssm.ps1` → (3) EC2에서 `deploy_api_on_server.sh` → (4) 컨테이너 DATABASES·backlog-count 200 확인 → (5) 그 다음 Lambda/CloudWatch 확인.
