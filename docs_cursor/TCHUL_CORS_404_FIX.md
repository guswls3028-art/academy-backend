# tchul.com CORS + 404 오류 대응

## 현상

- **콘솔:** `Access to XMLHttpRequest at 'https://api.hakwonplus.com/...' from origin 'https://tchul.com' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header`
- **추가:** `StudentsLayout-117WR.js` 404 → `Failed to fetch dynamically imported module`

---

## 1. CORS — "No 'Access-Control-Allow-Origin' header"

### 원인

- **Django `prod.py`에는 이미 `https://tchul.com`, `https://www.tchul.com`이 `CORS_ALLOWED_ORIGINS`에 포함되어 있음.**
- CORS 에러가 나는 대부분의 경우, **요청이 Django까지 가지 않고 502/503/504가 나가는 경우**임.  
  → 프록시(nginx)나 ALB가 502를 반환하면 그 응답에는 CORS 헤더가 없어서 브라우저가 "CORS policy"로만 표시함.

### 점검 순서

1. **API 직접 호출**
   - 브라우저 또는 터미널에서  
     `curl -I -X OPTIONS "https://api.hakwonplus.com/api/v1/core/me/" -H "Origin: https://tchul.com"`  
   - **200** + `Access-Control-Allow-Origin: https://tchul.com` 이면 CORS 설정은 정상.  
   - **502/503/504** 이면 인프라/업스트림 문제.

2. **502일 때**
   - API 서버(EC2) 프로세스 정상 여부, 타임아웃, ALB 타겟 그룹 상태 확인.
   - nginx 502 시 CORS 헤더 보강이 되어 있는지 확인:  
     `backend/infra/nginx/academy-api.conf` 의 `location @502_cors` 에  
     `add_header Access-Control-Allow-Origin $http_origin always;` 등이 있으면, 502 응답에도 CORS가 붙어서 브라우저는 "CORS" 대신 502로 인지할 수 있음.  
     (동작만 바꾸는 것이고, 근본 해결은 502를 없애는 것.)

3. **추가 도메인**
   - 새 프론트 도메인을 쓰는 경우에만 `prod.py`의 `CORS_ALLOWED_ORIGINS`(및 필요 시 `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS`)에 해당 origin 추가.

---

## 2. 404 — StudentsLayout-117WR.js / Failed to fetch dynamically imported module

### 원인

- 프론트는 `StudentsLayout`을 **lazy import** 하므로 빌드 시 해시된 청크 파일(예: `StudentsLayout-XXXXX.js`)이 생성됨.
- **이전 빌드의 `index.html`**이 캐시되어 있거나, 배포가 **새 index.html 없이** 되어 있으면,  
  HTML에는 **예전 해시**(`StudentsLayout-117WR.js`)만 적혀 있고, 실제 서버에는 **새 해시**의 파일만 있어서 404 발생.

### 대응

1. **배포 측**
   - 프론트 배포 시 **반드시 새 `index.html`이 올라가도록** 배포.
   - `index.html`은 캐시하지 않거나 짧게(예: `indexMaxAge: "0"`).  
     (params.yaml `front.cdnCacheControl.indexMaxAge` 참고.)
   - CDN/캐시를 쓰면 배포 후 **index.html 캐시 purge** 실행.

2. **사용자**
   - **강력 새로고침** (Ctrl+Shift+R 또는 Cmd+Shift+R) 또는 시크릿 창에서 `https://tchul.com/admin/students` 접속.
   - 그래도 404면, 배포가 완료된 뒤 다시 시도.

3. **선택: 런타임 안내**
   - 동적 import 실패 시 "최신 배포가 반영되지 않았을 수 있습니다. 새로고침(Ctrl+Shift+R) 해 주세요." 같은 메시지를 띄우도록 프론트에서 처리 가능.

---

## 요약

| 현상 | 원인 | 조치 |
|------|------|------|
| CORS blocked from tchul.com | 대부분 **502 등으로 API 응답이 Django를 안 거침** | API/인프라 정상화, 502 해소. 필요 시 nginx 502 시 CORS 보강 배포. |
| StudentsLayout-*.js 404 | **옛 index.html**이 옛 청크 이름을 참조 | index.html 갱신 배포, index 캐시 없음/purge, 사용자 강력 새로고침. |

**참고:**  
- CORS 설정 코드: `backend/apps/api/config/settings/prod.py` (이미 tchul.com 포함)  
- 502 시 CORS 보강: `backend/docs_cursor/CORS_502_TROUBLESHOOTING.md`, `backend/infra/nginx/academy-api.conf`
