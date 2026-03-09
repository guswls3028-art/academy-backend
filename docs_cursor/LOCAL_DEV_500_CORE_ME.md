# 로컬 개발 시 core/me, core/program 500 에러 대응

## 현상

- 브라우저(또는 프론트)에서 `GET http://localhost:5174/api/v1/core/me/` 또는 `GET .../api/v1/core/program/` 호출 시 **500 (Internal Server Error)** 발생.
- 프론트는 Vite(5174)에서 `/api`를 백엔드(localhost:8000)로 프록시 중.

## 원인 요약

1. **테넌트 미해석**  
   요청의 Host가 `localhost`(또는 `127.0.0.1`)인데, DB에 해당 Host → Tenant 매핑(**TenantDomain**)이 없으면:
   - 테넌트 해석 실패 시 **404** (hint: `ensure_localhost_tenant` 실행 권장)
   - 해석 과정에서 **DB/마이그레이션 오류**가 나면 **500**

2. **DB/마이그레이션**  
   - `TenantDomain` 테이블 없음(마이그레이션 미적용) → 쿼리 예외 → **500**
   - DB 연결 실패 → **500**

3. **뷰 내부 예외**  
   - 테넌트는 해석됐지만 `UserSerializer` / `program_get_by_tenant` / `ProgramPublicSerializer` 등에서 예외 발생 시 **500**

## 해결 절차

### 1) 백엔드·DB 확인

- 백엔드 서버가 **8000** 포트에서 떠 있는지 확인.
- DB 접속 가능한지, 마이그레이션 적용 여부 확인:

```powershell
cd C:\academy\backend
# 가상환경 등 사용 중이면 활성화 후
python manage.py migrate
```

### 2) localhost → 테넌트 매핑 생성 (필수)

로컬에서 Host `localhost`(또는 `127.0.0.1`)로 들어오는 요청이 하나의 테넌트로 해석되도록 매핑을 만듭니다.

```powershell
cd C:\academy\backend
python manage.py ensure_localhost_tenant
# 특정 테넌트로 고정하려면:
python manage.py ensure_localhost_tenant --tenant=1
```

- 이 명령은 `TenantDomain`에 `localhost`, `127.0.0.1` → (지정한 또는 첫 번째 활성) 테넌트를 등록합니다.
- **한 번만 실행**하면 되며, 테넌트가 없으면 “Create a tenant first” 안내가 나옵니다.

### 3) 테넌트/Program 없을 때

- **테넌트가 하나도 없으면** 먼저 Django admin 또는 `tenants/create` 등으로 테넌트를 만든 뒤 `ensure_localhost_tenant` 실행.
- `/core/program/`은 **테넌트당 Program 1개**가 있어야 200을 반환합니다. Program이 없으면 **404** (`program_missing`).  
  → 운영에서는 테넌트 생성 시 Program도 생성되도록 되어 있으므로, 로컬에서도 테넌트 생성 플로우를 타면 됨.

### 4) 여전히 500일 때

- **백엔드 터미널/로그**에 찍힌 **traceback**을 확인합니다.
  - 미들웨어에서 500이면: `Tenant resolution unexpected error` 등과 함께 예외 메시지가 나옴 (대개 DB/테이블/연결 문제).
  - 뷰에서 500이면: `MeView get failed` 또는 `ProgramView get ... failed` 등과 함께 serializer/DB 예외가 나옴.
- 마이그레이션 적용 여부 재확인: `python manage.py showmigrations core` 등으로 `core` 앱 마이그레이션 적용 상태 확인.

## 정리

| 단계 | 확인/실행 |
|------|-----------|
| 1 | 백엔드 8000 기동, DB 연결·마이그레이션 적용 |
| 2 | `python manage.py ensure_localhost_tenant` 실행 (localhost → 테넌트 매핑) |
| 3 | 테넌트·Program 존재 여부 확인 (없으면 생성) |
| 4 | 500 지속 시 백엔드 로그의 traceback으로 원인 확인 |

이후에는 로컬에서 `GET /api/v1/core/me/`(인증 필요), `GET /api/v1/core/program/`(AllowAny)가 200 또는 401/404로만 응답하고, 500이 나오지 않아야 합니다.
