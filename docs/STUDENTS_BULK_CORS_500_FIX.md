# 학생 일괄 등록(엑셀) CORS·500 대응

**증상:** `https://hakwonplus.com` 에서 학생 등록 → 엑셀 업로드 시  
- 콘솔: `Access to XMLHttpRequest at 'https://api.hakwonplus.com/api/v1/students/bulk' ... blocked by CORS policy: No 'Access-Control-Allow-Origin' header`  
- 동시에 `POST .../students/bulk net::ERR_FAILED 500 (Internal Server Error)`

---

## 1. CORS (해결 방향)

- **원인:** 500 응답에 CORS 헤더가 붙지 않으면 브라우저가 응답을 버리고 "CORS policy"로만 표시함.
- **백엔드 조치 (이미 반영):**
  - `apps/api/common/middleware.CorsResponseFixMiddleware` 추가  
    → 모든 응답에서 `Access-Control-Allow-Origin` 이 없고, 요청 `Origin` 이 `CORS_ALLOWED_ORIGINS` 에 있으면 CORS 헤더 보강.
  - `settings/base.py` 의 `MIDDLEWARE` 맨 앞에 `CorsResponseFixMiddleware` 등록.
- **배포 후:** API 서버 재시작(gunicorn/uvicorn 등) 후 다시 요청하면, 500이 나와도 CORS 헤더가 붙어서 브라우저가 응답 본문을 볼 수 있음.

---

## 2. 500 Internal Server Error (원인 확인)

엑셀 일괄 등록은 다음 엔드포인트를 사용함.

- **엑셀 업로드:** `POST /api/v1/students/bulk_create_from_excel/`  
  - multipart: `file`(엑셀), `initial_password`  
  - R2 업로드 후 `excel_parsing` job 디스패치, 202 반환.

프론트가 `POST /api/v1/students/bulk` 로 호출하는 경우, 백엔드에는 **`/bulk`** 경로가 없고 **`/bulk_create_from_excel/`** 만 있음.  
- `/bulk` → 404 가능성 있음 (DRF가 404를 500으로 보낼 수는 있으나, 보통 404 반환).  
- 실제로 500이 난다면 서버 로그로 예외를 확인해야 함.

**서버 로그 확인 (API 서버):**

```bash
# gunicorn/uvicorn 로그 또는 systemd/cron 로그
# 예외 메시지·traceback 확인
```

**500이 날 수 있는 흐름 (bulk_create_from_excel 기준):**

| 구간 | 가능 원인 |
|------|------------|
| tenant | `request.tenant` 없음 → 400 반환. 500이면 보통 그 이후 단계. |
| R2 업로드 | `R2_EXCEL_BUCKET` / `EXCEL_BUCKET_NAME`, R2 자격증명 오류, 권한 오류. |
| job 디스패치 | SQS/Redis 등 메시지 전송 실패, `dispatch_job` 내부 예외. |
| 설정/import | `upload_fileobj_to_r2_excel`, `dispatch_job` import 경로·환경 오류. |

**권장:**  
1. API 서버 로그에서 `POST /api/v1/students/bulk_create_from_excel/` (또는 `/students/bulk`) 요청 시의 **traceback** 확인.  
2. CORS 수정 배포 후 같은 요청을 다시 보내서, 브라우저 네트워크 탭에서 **500 응답 본문** 확인 (JSON의 `detail`, `error` 등).

---

## 3. 프론트 URL 확인

- 백엔드 경로: `bulk_create_from_excel` → **`/api/v1/students/bulk_create_from_excel/`** (끝 슬래시 포함 여부는 DRF 설정 따름).
- 프론트가 **`/api/v1/students/bulk`** 로만 호출한다면 **404**가 나올 수 있으므로,  
  엑셀 일괄 등록 요청 URL을 **`/api/v1/students/bulk_create_from_excel/`** 로 맞추는지 확인.

---

## 4. 요약

- **CORS:** `CorsResponseFixMiddleware` 로 5xx에도 CORS 헤더 보강됨. 배포·재시작 후 재요청.
- **500:** 서버 로그와 500 응답 본문으로 예외 확인 후, R2·job 디스패치·tenant·설정 순으로 점검.
- **URL:** 엑셀 일괄 등록은 `POST /api/v1/students/bulk_create_from_excel/` 사용.
