# 학생앱–백엔드 커뮤니티 스펙 검증 리뷰

코드 단위로 학생앱(공지·QnA)과 백엔드 Community API의 스펙 일치 여부를 검증한 결과입니다.

---

## 1. API 진입점·테넌트

| 항목 | 백엔드 | 학생앱(프론트) | 일치 |
|------|--------|-----------------|------|
| Community prefix | `path("community/", include(...))` → `/api/v1/community/` | `PREFIX = "/community"`, baseURL=`/api/v1` | ✅ |
| 테넌트 해석 | Host 기반 + 학생 요청 시 `get_request_student(request).tenant` 폴백 | axios 공통(tenant 헤더/호스트 등 기존 방식) | ✅ |

---

## 2. 공지 (Notices)

### 2.1 공지 목록

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 엔드포인트 | `GET /community/posts/notices/` | `fetchNoticePosts({ pageSize: 50 })` → 동일 URL | ✅ |
| 쿼리 파라미터 | `page`, `page_size` (기본 50, 최대 200) | `pageSize: 50` (1페이지만 요청) | ✅ |
| 응답 형태 | `PostEntitySerializer` 배열 (페이지네이션 없이 해당 페이지만) | `Array.isArray(data) ? data : []` | ✅ |
| 데이터 소스 | `get_notice_posts_for_tenant(tenant)`, `block_type__code__iexact="notice"` | 관리자 공지와 동일 API 사용 | ✅ |

- **참고:** 공지 50건 초과 시 학생앱은 2페이지를 요청하지 않음. 1페이지(50건)만 표시. 필요 시 `page` 파라미터로 추가 요청하도록 확장 가능.

### 2.2 공지 상세

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 엔드포인트 | `GET /community/posts/:id/` | `fetchNoticeDetail(id)` → `fetchPost(id)` | ✅ |
| 학생 접근 허용 | **수정 반영:** `retrieve()`에서 학생은 (1) 공지(block_type code=notice) 또는 (2) 본인 작성 글만 조회 가능 | `NoticeDetailPage`에서 `fetchNoticeDetail(noticeId)` 호출 | ✅ (수정 후) |

- **수정 내용:** 기존에는 `get_queryset()`이 학생 요청 시 `created_by=request_student`만 반환해, 공지(created_by=staff) 상세가 404가 났음. `PostViewSet.retrieve()`를 오버라이드하여 학생이 **공지** 또는 **본인 작성 글**만 단건 조회하도록 변경함.

---

## 3. QnA (학생 “내 질문”)

### 3.1 내 질문 목록

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 엔드포인트 | `GET /community/posts/?page_size=...` (node_id 없음) | `fetchMyQnaQuestions({ pageSize: 50 })` → `fetchPosts({ nodeId: null, pageSize })` | ✅ |
| 백엔드 필터 | `get_all_posts_for_tenant(tenant).filter(created_by=request_student)` | - | ✅ |
| 응답 형태 | `list()`에서 페이지네이션 없이 배열 반환 | `fetchPosts`가 `results` 또는 배열 모두 처리 | ✅ |
| QnA 식별 | block_type code="qna" (백엔드 필터 없음, 전체 본인 글 반환) | `getQnaBlockTypeId()` 후 `post.block_type === qnaBlockTypeId` 또는 라벨 fallback | ✅ |

### 3.2 질문 상세

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 엔드포인트 | `GET /community/posts/:id/` | `fetchQnaQuestionDetail(questionId)` → `fetchPost(id)` | ✅ |
| 학생 접근 | `retrieve()`에서 본인 작성 글 허용 (위 2.2와 동일 로직) | 본인 질문만 상세 진입 | ✅ |

### 3.3 질문 작성

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 엔드포인트 | `POST /community/posts/` | `createPost({ block_type, title, content, created_by, node_ids: [] })` | ✅ |
| body | `block_type`, `title`, `content`, `node_ids`, (선택) `created_by` | 동일 (학생은 `node_ids: []`) | ✅ |
| created_by | 학생 요청 시 `request_student`로 고정, QnA 시 null이면 400 + `profile_required` | `profile`/캐시에서 `me.id` 전달, 400 시 프로필 재조회 | ✅ |
| node_ids=[] | `CommunityService.create_post`에서 빈 매핑 허용 | 질문 작성 폼에서 `node_ids: []` 전달 | ✅ |

### 3.4 답변 목록·등록

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 답변 목록 | `GET /community/posts/:id/replies/` | `fetchPostReplies(postId)` | ✅ |
| 응답 필드 | `id`, `post`, `question`(post_id), `content`, `created_by`, `created_by_display`, `created_at` | `Answer`: `question`(post_id), `created_by_display` 등 사용 | ✅ |
| 답변 등록 | `POST /community/posts/:id/replies/` body `{ content }` | `createAnswer(questionId, content)` (선생/관리자용, 학생은 조회만) | ✅ |

---

## 4. 공통 타입·직렬화

| 백엔드 serializer 필드 | 프론트 타입(PostEntity 등) | 비고 |
|-------------------------|----------------------------|------|
| id, tenant, block_type, block_type_label, title, content | 동일 | ✅ |
| created_by, created_by_display, created_by_deleted, created_at | 동일 (updated_at은 백엔드 미포함, 프론트 optional) | ✅ |
| replies_count, mappings | 동일, mappings[].node_detail (ScopeNodeMinimal) | ✅ |
| ScopeNodeMinimal: id, level, lecture, session, lecture_title, session_title | level "COURSE" \| "SESSION" (백엔드 choices와 일치) | ✅ |

---

## 5. Block types

| 항목 | 백엔드 | 학생앱 | 일치 |
|------|--------|--------|------|
| 목록 | `GET /community/block-types/` (DRF 기본 페이지네이션 가능) | `fetchBlockTypes()` → `results` 또는 배열 처리 | ✅ |
| QnA/공지 코드 | code="qna", "notice" (없으면 list 시 자동 생성) | `getQnaBlockTypeId()`, `getNoticeBlockTypeId()` | ✅ |

---

## 6. 테넌트 격리

- 모든 쿼리: `tenant`는 `request.tenant` 또는 `get_request_student(request).tenant`로만 결정.
- 단건 조회: `get_post_by_id(tenant, pk)`로 동일 테넌트 내에서만 조회.
- 학생 list: `created_by=request_student`로 본인 글만 노출.
- 공지 list: `get_notice_posts_for_tenant(tenant)`로 테넌트 공지만 노출.
- Cross-tenant 노출 없음.

---

## 7. 수정 사항 요약

- **Backend `apps/domains/community/api/views.py`**
  - `_get_tenant_from_request(request)` 헬퍼 추가.
  - `PostViewSet.retrieve()` 오버라이드: 학생인 경우 **(1) 공지(block_type code=notice)** 또는 **(2) 본인 작성 글(created_by=request_student)** 만 200, 그 외 404.

---

## 8. 검증 결과 요약

| 영역 | 스펙 일치 | 비고 |
|------|-----------|------|
| 공지 목록 | ✅ | 1페이지(50건)만 사용, 추가 페이지 필요 시 프론트 확장 |
| 공지 상세 | ✅ | retrieve 권한 수정 반영 |
| QnA 목록/상세/작성 | ✅ | node_ids=[], created_by 처리 일치 |
| 답변 목록 | ✅ | - |
| 타입·직렬화 | ✅ | PostEntity, Answer, ScopeNodeMinimal 일치 |
| 테넌트 격리 | ✅ | tenant/학생 필터 일관 적용 |

**결론:** 학생앱 공지·QnA는 백엔드 Community API와 코드 단위로 스펙이 맞으며, 공지 상세 조회 404 문제는 위 `retrieve()` 수정으로 해소됨.
