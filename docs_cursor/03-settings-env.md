# 설정 · 환경 변수 (실제 코드 기준)

**위치**: `apps/api/config/settings/base.py`, `prod.py`.

---

## 1. TENANT_BYPASS_PATH_PREFIXES (base.py)

tenant 미해결 허용 경로:

```
/admin/
/api/v1/token/
/api/v1/token/refresh/
/internal/
/api/v1/internal/
/swagger
/redoc
```

---

## 2. CORS (base / prod)

- `CORS_ALLOW_ALL_ORIGINS = False`, `CORS_ALLOW_CREDENTIALS = True`.
- **CORS_ALLOWED_ORIGINS** (base):  
  `http://localhost:5173`, `http://localhost:5174`, `https://hakwonplus.com`, `https://www.hakwonplus.com`, `https://academy-frontend.pages.dev`, `https://limglish.kr`, `https://www.limglish.kr`, `https://tchul.com`, `https://www.tchul.com`, `https://ymath.co.kr`, `https://www.ymath.co.kr`, `https://dev-web.hakwonplus.com`.
- **prod**: 위와 유사, localhost 5173 제외, `http://localhost:5174` 포함(배포 API + 로컬 프론트용).
- **CORS_ALLOW_HEADERS**: default_headers + `X-Client-Version`, `X-Client`.

---

## 3. CSRF_TRUSTED_ORIGINS

- **base**: hakwonplus, limglish, tchul, ymath (https + www), academy-frontend.pages.dev, `https://*.trycloudflare.com`.
- **prod**: hakwonplus, limglish, tchul, ymath (https + www), academy-frontend.pages.dev (localhost/trycloudflare 제외).

---

## 4. ALLOWED_HOSTS (base 예시)

`127.0.0.1`, `localhost`, `hakwonplus.com`, `www.hakwonplus.com`, `api.hakwonplus.com`, `limglish.kr`, `.limglish.kr`, `academy-frontend.pages.dev`, `.trycloudflare.com`, `dev-web.hakwonplus.com`, `dev-api.hakwonplus.com` 등. prod는 별도 정의.

---

## 5. DB

- `ENGINE`: django.db.backends.postgresql
- ENV: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`(기본 5432), `DB_CONN_MAX_AGE`(기본 60).

---

## 6. 기타 ENV (배포·워커)

- `SECRET_KEY`, `DEBUG`
- `AWS_REGION`, `AWS_DEFAULT_REGION`
- `AI_WORKER_INSTANCE_ID`, `VIDEO_WORKER_INSTANCE_ID`
- R2/CDN 등: `apps/api/config/settings/base.py` 내 변수 참고.

---

## 7. Proxy / Host

- `USE_X_FORWARDED_HOST = True`
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`

새 도메인 추가 시: `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` 모두 반영 필요.
