# E2E 검증 사실 리포트 (Application-Level)

**생성일시:** 2026-03-06  
**목적:** healthz/단일 API 200 외 실제 사용자 플로우 기준 검증

---

## 1. API 이미지 digest / tag

| 항목 | 값 |
|------|-----|
| ECR academy-api:latest digest | `sha256:c10ddf1be2e5fd72d62c569dcafe2fa253a117a551a96ea6140d65ad63be1764` (푸시: 2026-03-06 17:22) |
| 인스턴스 실행 중 digest (대화 요약 기준) | `sha256:89470ae00f6a29bd303538553d1ca3a83a7eb3473b9767ec8b44b545fd70334c` |
| **결론** | **불일치** — 인스턴스는 ECR latest와 다른 구버전 이미지 사용 중 |

---

## 2. 실행 이미지 vs prod.py / CORS / ALLOWED_HOSTS

| 항목 | 상태 |
|------|------|
| prod.py 변경 (ALB hostname, CORS regex 등) | 코드에 반영됨 |
| ECR latest | 2026-03-06 17:22 푸시됨 (prod.py 포함 여부는 빌드 시점에 따름) |
| **결론** | **인스턴스가 구버전 이미지 사용** — instance refresh로 ECR latest 반영 필요 |

---

## 3. 프론트 빌드 산출물 (stale URL)

| 항목 | 값 |
|------|-----|
| .env.production | `VITE_API_BASE_URL=https://api.hakwonplus.com`, `VITE_MEDIA_CDN_BASE=https://cdn.hakwonplus.com` |
| dist/assets/*.js | `cdn.hakwonplus` 문자열 포함 확인 |
| **결론** | API URL·CDN URL 모두 최신 설정 반영. stale URL 없음. |

---

## 4. API SSM CDN/R2 설정

| 변수 | SSM 값 |
|------|--------|
| CDN_HLS_BASE_URL | `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev` |
| R2_PUBLIC_BASE_URL | `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev` |
| R2_VIDEO_BUCKET | academy-video |

---

## 5. 미디어/CDN URL 접근 가능 여부

| URL | HTTP 상태 | 비고 |
|-----|-----------|------|
| `https://cdn.hakwonplus.com` | **403 Forbidden** | Cloudflare 응답 |
| `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev` | 404 Not Found | 루트 리스트 비허용(정상) |
| `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev/tenants/1/video/hls/1/thumbnail.jpg` | 404 Not Found | 객체 미존재 또는 경로 상이 가능 |

---

## 6. URL 흐름 정리

| 출처 | thumbnail_url / hls_url 형식 |
|------|------------------------------|
| VideoSerializer (apps/support/video/serializers.py) | **절대 URL** `{CDN_HLS_BASE_URL}/{path}` → r2.dev |
| student_app media (domains/student_app/media) | 상대 경로 또는 `request.build_absolute_uri` |
| VideoThumbnail.tsx | 절대 URL이면 그대로 사용, 상대면 `VITE_MEDIA_CDN_BASE` + path |

- API가 r2.dev 절대 URL을 반환하면 프론트는 그대로 사용.
- student_app에서 상대 경로를 반환하면 프론트가 `cdn.hakwonplus.com` + path로 요청 → **403**.

---

## 7. 업로드 플로우 (코드 기준)

| 단계 | 경로 | 동작 |
|------|------|------|
| 1. init | POST /api/v1/media/videos/upload/init/ | DB row 생성, presigned PUT URL 반환 |
| 2. PUT | presigned URL (R2) | 프론트 → R2 직접 업로드 |
| 3. complete | POST /api/v1/media/videos/{id}/upload/complete/ | API가 presigned GET으로 워커에 전달 |

- presigned URL은 R2 endpoint 사용 → API·R2 인증이 정상이면 동작 가능.
- **실제 E2E 호출 검증은 인증 필요(401)** — 수동/브라우저 검증 필요.

---

## 8. 비디오 관련 API (인증 필요)

| 엔드포인트 | 테스트 결과 |
|------------|-------------|
| GET /api/v1/media/videos/ | 401 Unauthorized (X-Tenant-Code만으로 부족) |

- 로그인/세션 기반 인증 필요. 브라우저에서 실제 사용자 플로우로 검증 필요.

---

## 9. 정확한 실패 지점 (사실 기반)

| # | 실패 지점 | 근거 |
|---|-----------|------|
| 1 | **API 이미지 구버전** | 인스턴스 digest ≠ ECR latest |
| 2 | **cdn.hakwonplus.com 403** | 미디어 요청 시 상대 경로 사용 시 실패 |
| 3 | **r2.dev 접근** | 404 — 객체 없거나 경로 불일치 가능 |

---

## 10. 수정 권장 사항

### 10.1 API 이미지 동기화 (우선)

- ASG instance refresh 또는 재배포로 ECR latest 이미지 사용
- CI 빌드(OIDC) 복구 후 재배포

### 10.2 cdn.hakwonplus.com 403 해결

- Cloudflare R2 대시보드에서 custom domain `cdn.hakwonplus.com` → academy-video 버킷 연결 확인
- Cloudflare Access / WAF 등으로 익명 접근 차단 여부 확인

### 10.3 API/SSM CDN_HLS_BASE_URL 정렬

- cdn.hakwonplus.com이 정상 동작하면 SSM `CDN_HLS_BASE_URL`를 `https://cdn.hakwonplus.com`으로 변경
- API가 cdn 도메인 기반 절대 URL을 반환하도록 통일

### 10.4 API 이미지 강제 갱신 (instance refresh)

- LT UserData는 `:latest`를 사용하므로 LT diff가 없으면 instance refresh가 자동 실행되지 않음.
- **수동 실행:**
  ```powershell
  aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg --region ap-northeast-2 --profile default
  ```
- **전제:** CI 빌드 복구 후 ECR에 새 이미지가 푸시되어 있어야 함.

### 10.5 실제 사용자 플로우 검증 (수동)

- 로그인 → 비디오 목록 → 썸네일 로드 → 업로드 → 재생
- 브라우저 DevTools Network/Console에서 실패 요청·에러 수집

---

## 11. 증명(proof) 체크리스트

| 항목 | 검증 방법 | 완료 |
|------|-----------|------|
| API 이미지 digest | `aws ec2 describe-instances` + `aws ecr describe-images` | ✅ |
| prod.py 반영 여부 | CI 빌드 실패 + 코드 diff | ✅ |
| 프론트 stale URL | .env.production + dist 검사 | ✅ |
| cdn.hakwonplus.com | curl -sI | ✅ 403 |
| r2.dev 경로 | curl -sI 특정 path | ✅ 404 |
| 업로드 E2E | 인증 필요 — 수동 | ⏳ |
| 비디오 API | 인증 필요 — 수동 | ⏳ |
| 브라우저 에러 | 수동 DevTools | ⏳ |
