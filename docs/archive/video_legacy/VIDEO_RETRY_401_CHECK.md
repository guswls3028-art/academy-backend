# 재처리(retry) 401 체크 — 인증 실패 시 Batch까지 안 감

## 1. 재처리 API (코드 기준)

| 항목 | 값 |
|------|-----|
| **Retry API URL** | `POST https://api.hakwonplus.com/api/v1/media/videos/<video_id>/retry/` |
| **예시** | `POST https://api.hakwonplus.com/api/v1/media/videos/123/retry/` |
| **코드** | `apps/support/video/views/video_views.py` L490–550 (`VideoViewSet.retry`) |
| **인증** | `JWTAuthentication` + `CsrfExemptSessionAuthentication` |
| **권한** | `IsAuthenticated` + `TenantResolvedAndStaff()` (retry는 STAFF_ACTIONS에 포함) |

흐름: **retry API 200/202** → `create_job_and_submit_batch(video)` → `submit_batch_job()` → DB job 생성 + Batch 제출.  
**401이면 여기까지 못 감** → DB job 없음, Batch job 없음.

---

## 2. DevTools에서 확인할 3가지

1. **Retry API URL**  
   재처리 버튼 클릭 시 Network에 찍히는 요청 URL이 아래와 같은지 확인.
   - `https://api.hakwonplus.com/api/v1/media/videos/<숫자>/retry/`

2. **Retry API status code**  
   그 요청의 Status가 **200/202**인지 **401**인지 확인.

3. **Retry API response body**  
   - 401이면 보통: `{"detail":"Authentication credentials were not provided."}` 또는 `"Given token not valid for any token type"` 등.
   - 403이면 권한(테넌트/스태프) 문제 가능.

추가로 **Request headers**에서:
- `Authorization: Bearer <access_token>` 있는지
- 또는 쿠키로 세션 인증하는 경우 `Cookie`에 세션 쿠키 있는지 확인.

---

## 3. /core/me/ 401과의 관계

- `api.hakwonplus.com/api/v1/core/me/` 가 401이면:
  - **가능성 A:** 로그인 만료/토큰 만료 → 프론트가 “비로그인”으로 간주 → 이후 요청에 토큰을 안 넣거나 막아서 **retry도 401**.
  - **가능성 B:** 프론트가 **먼저 /core/me/ 호출**하고, 실패하면 retry 요청 자체를 안 보낼 수 있음.
- 따라서 **retry 요청이 Network에 아예 없으면** → 프론트가 me 실패 후 요청 생략한 것일 수 있음.  
  **retry 요청은 있는데 401**이면 → 서버에서 인증 실패(토큰/세션/DRF permission).

---

## 4. 서버 로그로 확인

재처리 클릭 시:

- `POST /api/v1/media/videos/<id>/retry/` 가 **찍히는지** 확인.
  - 안 찍히면: 요청이 API 서버까지 안 감(프론트에서 안 보냄, CORS, 또는 중간에서 401로 끊김).
  - 찍히는데 401이면: DRF 인증/권한 단계에서 거절(토큰/세션/`TenantResolvedAndStaff`).

---

## 5. 가능성 높은 원인 TOP 3

1. **로그인/토큰 만료** — access 만료 후 refresh 실패 → `/core/me/` 401 → retry에도 토큰 없음 → 401.
2. **도메인/쿠키** — 프론트가 `hakwonplus.com`, API가 `api.hakwonplus.com` 이면 쿠키 미전송 가능 → 세션 인증 실패.
3. **TenantResolvedAndStaff** — Host로 테넌트 해석 실패 또는 해당 테넌트 스태프가 아님 → 403 가능(401이면 보통 인증 단계에서 실패).

---

## 6. 지금 필요한 정보 (3가지)

브라우저 Network에서 재처리 클릭 후:

1. **Retry API URL** — 실제 찍힌 URL (예: `.../media/videos/123/retry/`).
2. **Retry API status code** — 200/202/401/403 등.
3. **Retry API response body** — 한 줄이라도 (예: `{"detail":"Authentication credentials were not provided."}`).

이 3가지만 알려주면 원인 더 좁힐 수 있음.
