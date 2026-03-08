# QnA 질문 등록: 로컬은 되고 배포에서만 안 될 때

## 전제

- **로컬 DB와 배포 DB는 동일한 RDS**를 가리킨다 (이미 조회로 확인됨).
- 따라서 “배포에서만 안 된다”는 **DB가 달라서**가 아니라, **요청 처리 경로(테넌트·인증·created_by)** 차이 때문일 가능성이 크다.

---

## 1. 배포에서만 실패할 수 있는 원인 (코드/설계 기준)

### (1) 403 "tenant required"

- **원인:** API 서버에서 `request.tenant`가 `None`으로 남는 경우.
- **테넌트 해석 경로** (`apps/core/tenant/resolver.py`):
  - `Host`가 `api.hakwonplus.com`이고 `X-Tenant-Code`가 있으면 → 헤더로 테넌트 결정.
  - 그 외 → `request.get_host()`로 `TenantDomain.host` 조회.
- **배포에서만 발생 가능한 경우:**
  - 학생 앱이 `api.hakwonplus.com`으로 API를 호출하는데,
    - DB에 `api.hakwonplus.com`에 해당하는 `TenantDomain`이 없고,
    - 프론트에서 **X-Tenant-Code를 안 보내거나**, **잘못된 코드**를 보낼 때.
  - 로컬은 `Host=localhost` + (필요 시) `TenantDomain(localhost)` 또는 로컬용 테넌트 코드로 동작하므로 403이 안 날 수 있음.

**확인:**  
배포 환경에서 질문 등록 요청 시 API 응답이 403인지, 응답 본문에 `tenant required` / `tenant resolution failed` 등이 있는지 확인.

---

### (2) 401 Unauthorized (인증 실패)

- **원인:** `POST /api/v1/community/posts/`는 기본 `IsAuthenticated` + JWT/Session 인증을 사용.  
  배포에서만 JWT가 안 넘어가거나 만료되면 401.
- **배포에서만 발생 가능한 경우:**
  - 학생 앱 도메인과 API 도메인이 다를 때 (예: `app.hakwonplus.com` → `api.hakwonplus.com`) 쿠키 미전송.
  - 토큰을 저장/전달하는 방식이 로컬과 다르거나, 배포 빌드에서 토큰이 빠지는 경우.

**확인:**  
브라우저 네트워크 탭에서 `POST .../community/posts/` 요청에 `Authorization: Bearer ...`가 붙는지, 401 응답이 오는지 확인.

---

### (3) 201 Created 인데 created_by=null (등록은 되는데 “내 질문”에 안 보임)

- **문서화된 원인:**  
  `fix_qna_orphan_created_by` 관리 명령 docstring:  
  **“학생앱에서 프로필 로드 전 제출 시 created_by가 비어 저장됨.”**
- **동작:**  
  `PostViewSet.create()`에서 `created_by`는 `get_request_student(request)`로 채운다.  
  학생이면 `request.user.student_profile`, 학부모면 연결된 첫 학생.  
  이게 `None`이면 글이 `created_by=null`로 저장되고,  
  **“내 질문” 목록은 `created_by=request_student`로 필터하므로** 목록에 안 나온다.
- **배포에서만 발생 가능한 경우:**
  - 프론트에서 **프로필(/me 또는 학생 정보) 로드 전에** 제출 버튼을 눌러서,  
    백엔드에는 인증은 되었지만 `request.user.student_profile`이 아직 없거나,  
    (같은 DB라도) 세션/요청 차이로 학생 프로필이 매핑되지 않은 경우.
  - 로컬에서는 같은 플로우에서 프로필이 먼저 로드되어 있어서 정상 동작하는 경우.

**확인:**  
- API 로그에서 해당 요청이 **201**인지.  
- DB에서 최근 QnA PostEntity에 `created_by_id`가 null인 행이 있는지  
  (`list_qna_posts` 관리 명령 또는 `fix_qna_orphan_created_by --dry-run`).

---

## 2. 점검 순서 (사실 확인용)

1. **배포에서 질문 등록 시 HTTP 상태 코드 확인**  
   - 403 → 테넌트 해석 실패 (Host / X-Tenant-Code, TenantDomain 테이블).  
   - 401 → 인증(JWT/세션) 문제.  
   - 201 → (3)번 created_by=null 가능성으로 DB/목록 확인.

2. **403이면**  
   - API 서버 로그의 `tenant resolution failed` / `tenant_invalid` 등 메시지 확인.  
   - DB의 `TenantDomain`: `api.hakwonplus.com` 또는 사용 중인 Host에 대한 행 존재 여부.  
   - 프론트: 배포 도메인에서 `getTenantCodeForApiRequest()`가 어떤 값을 쓰는지,  
     해당 값이 백엔드 테넌트 코드와 일치하는지 확인.

3. **201인데 “내 질문”에 안 보이면**  
   - 최근 QnA PostEntity의 `created_by_id`가 null인지 확인.  
   - null이면 `fix_qna_orphan_created_by`로 정리 가능.  
   - 재발 방지: 학생 앱에서 **질문 제출 전에 프로필/학생 정보 로드 완료 후** 제출하도록 플로우 수정 (버튼 비활성화 또는 프로필 로드 대기).

4. **401이면**  
   - 배포 환경에서 Authorization 헤더·토큰 저장소(메모리/로컬스토리지 등)가 로컬과 동일한지,  
   - 도메인/경로가 바뀌면서 토큰이 빠지지 않는지 확인.

---

## 3. 참고 코드 위치

- 테넌트 해석: `apps/core/middleware/tenant.py`, `apps/core/tenant/resolver.py`
- 질문 생성·created_by: `apps/domains/community/api/views.py` → `PostViewSet.create()`, `get_request_student(request)`
- created_by=null 정리: `apps/domains/community/management/commands/fix_qna_orphan_created_by.py`
- 프론트 X-Tenant-Code: `src/shared/api/axios.ts` (interceptor), `src/shared/tenant/index.ts` → `getTenantCodeForApiRequest()`
