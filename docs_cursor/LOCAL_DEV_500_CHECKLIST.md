# 로컬 개발 시 /api/v1/core/me/·/core/program/ 500 대응

## 현상

- 프론트(localhost:5174)에서 `GET http://localhost:5174/api/v1/core/me/` 또는 `GET .../api/v1/core/program/` 호출 시 **500 Internal Server Error**.

## 원인 후보

1. **localhost → 테넌트 미등록**  
   Vite 프록시가 `/api`를 `localhost:8000`으로 보내므로, 백엔드는 `Host: localhost:8000`으로 받고 `localhost`로 테넌트를 찾습니다.  
   **localhost / 127.0.0.1**에 해당하는 `TenantDomain`이 없으면:
   - 테넌트 해석 실패 시 **404** (메시지에 `Run: python manage.py ensure_localhost_tenant` 안내 포함)
   - 또는 그 이후 뷰/권한 단계에서 예외 시 **500**

2. **백엔드 미기동**  
   프론트만 켜고 백엔드를 8000에서 안 띄우면, 프록시가 연결 실패해 **ERR_CONNECTION_REFUSED** 또는 프록시 오류.

3. **DB/마이그레이션·데이터**  
   테넌트/프로그램/유저 조회·직렬화 중 예외 → **500**.  
   DEBUG=True이면 500 응답 본문에 `error` 필드로 예외 메시지가 포함됨.

## 체크리스트

| 순서 | 확인 | 조치 |
|------|------|------|
| 1 | 백엔드가 **8000**에서 떠 있는지 | `cd backend` 후 `.\run-dev-single.ps1` 또는 `python manage.py runserver 0.0.0.0:8000` |
| 2 | localhost 테넌트 등록 | `python manage.py ensure_localhost_tenant` (필요 시 `--tenant=1`) |
| 3 | 테넌트/프로그램 존재 | 최소 1개 활성 Tenant + 해당 테넌트에 Program 1개 (없으면 관리자/시그널로 생성) |
| 4 | 로그인/멤버십 | `/core/me/`는 **인증 + 해당 테넌트 멤버** 필요. 로그인 후 해당 테넌트 소속인지 확인 |

## 500 응답 본문으로 원인 보기

- **DEBUG=True**인 경우:
  - `MeView`/`ProgramView`에서 난 500 응답에 **`error`** 필드가 포함됨.
  - 브라우저 개발자 도구 → Network → 실패한 요청 → **Response** 탭에서 `error` 내용 확인.
- **tenant 해석 단계**에서 난 500이면 응답에 **`code`: `server_error`**, **`message`**에 예외 문자열이 있음.

## 요약

1. 백엔드 **localhost:8000** 기동  
2. **`python manage.py ensure_localhost_tenant`** 실행  
3. 500이 계속되면 응답 본문의 `error`/`message`와 백엔드 콘솔 로그 확인  
