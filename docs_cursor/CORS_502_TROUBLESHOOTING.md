# CORS 에러 + 502 Bad Gateway 대응

## 현상

- 브라우저: `Access to XMLHttpRequest at 'https://api.hakwonplus.com/...' from origin 'https://hakwonplus.com' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
- 일부 요청에 `502 (Bad Gateway)` 동시 표시.

## 원인

1. **502는 Django가 아니라 프록시(nginx, ALB 등)에서 반환**됩니다.  
   - 백엔드 다운, 타임아웃, 연결 실패 시 프록시가 502를 보내고, 이 응답에는 **CORS 헤더가 없습니다**.
2. **Preflight(OPTIONS) 요청도 502**가 나면 브라우저는 "preflight doesn't pass access control check"로 표시합니다.
3. **Django CORS 설정은 이미 올바름**: `CORS_ALLOWED_ORIGINS`(base.py, prod.py)에 `https://hakwonplus.com` 포함됨.  
   → 502가 나오지 않으면 정상 응답에는 CORS 헤더가 붙습니다.

정리하면, **표면은 CORS 에러지만 실제 원인은 502(API/프록시 장애)**입니다.

## 점검 순서

1. **API 서버 상태**
   - `api.hakwonplus.com` 호출 시 502가 나는지 확인 (브라우저/curl).
   - EC2에서 academy-api 컨테이너 실행 여부:  
     `sudo docker ps` / `sudo docker logs academy-api`.
   - Gunicorn/프로세스 다운, OOM, 재시작 루프 여부 확인.

2. **프록시/로드밸런서**
   - nginx 또는 ALB 로그에서 502 원인 확인 (upstream timeout, connection refused 등).
   - 타겟 그룹 health check 실패 여부 확인.

3. **CORS 설정 변경은 불필요**
   - `https://hakwonplus.com` 이미 허용됨.  
   - 502 구간을 해소하면 CORS 에러는 사라집니다.

## 502 해결 후

- API가 정상 응답하면 Django/CorsMiddleware가 `Access-Control-Allow-Origin: https://hakwonplus.com` 등을 붙입니다.
- 500 등 Django가 반환하는 에러는 `UnhandledExceptionMiddleware`에서 CORS 헤더를 추가하므로, 브라우저에서 동일한 CORS 에러로 가리지 않습니다.

## 참고

- 새 프론트 도메인 추가 시: `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` 모두 반영 (docs/REFERENCE.md).
