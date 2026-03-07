# QnA 학생 앱 ↔ 관리자 Inbox 연동 검증

## 개요

- **학생 앱**: `/student/qna` — 내 질문 목록, 질문 작성, 질문 상세·답변 보기
- **관리자 Inbox**: `/admin/community/qna` — 전체/강의별 QnA 목록, 답변 작성·수정·삭제
- **동일 백엔드**: `POST /api/v1/community/posts/`, `GET /api/v1/community/posts/:id/`, `GET/POST .../replies/` 등

## API 흐름

| 주체 | 목적 | 엔드포인트 | 비고 |
|------|------|------------|------|
| 학생 | 내 질문 목록 | `GET /api/v1/community/posts/` (node_id 없음) | 본인 글만 반환, 비페이지네이션 |
| 학생 | 질문 상세 | `GET /api/v1/community/posts/:id/` | tenant 내 권한 |
| 학생 | 답변 목록 | `GET /api/v1/community/posts/:id/replies/` | 동일 |
| 학생 | 질문 작성 | `POST /api/v1/community/posts/` | block_type=qna, created_by=학생 |
| 관리자 | QnA 목록(전체) | `GET /api/v1/community/admin/posts/?block_type_id=:qna&page_size=500` | AdminPostViewSet |
| 관리자 | 질문 상세 | `GET /api/v1/community/posts/:id/` | PostViewSet.retrieve |
| 관리자 | 답변 등록 | `POST /api/v1/community/posts/:id/replies/` | content |
| 관리자 | 답변 수정/삭제 | `PATCH/DELETE .../replies/:reply_id/` | reply_detail 액션 |

## 백엔드 동작 요약

1. **PostViewSet**
   - `GET /community/posts/`  
     - **학생**(`get_request_student` 존재) + `node_id` 없음 → **본인 작성 글만** 반환, **페이지네이션 없음**  
     - 그 외(관리자 또는 `node_id` 있음) → tenant 전체 또는 해당 노드, **페이지네이션 20**
   - `POST /community/posts/`: tenant·학생이면 `created_by` 자동 설정
   - `GET/POST /community/posts/:id/replies/`: 목록·등록
   - `PATCH/DELETE /community/posts/:id/replies/:reply_id/`: 수정·삭제

2. **AdminPostViewSet**
   - `GET /community/admin/posts/`: block_type_id, lecture_id, page, page_size로 관리자용 목록

## 프론트 연동

- **학생**: `fetchMyQnaQuestions()` → `fetchPosts({ nodeId: null, pageSize: 200 })` → `GET /community/posts/`  
  - 백엔드가 학생일 때 이미 본인 글만 비페이지네이션으로 주므로, 학생 앱은 그대로 전체 목록 사용 가능
- **관리자**: `fetchCommunityQuestions(scopeParams)`  
  - `scope === "all"` → `fetchAdminPosts({ blockTypeId: qna, pageSize: 500 })`  
  - 강의/차시 범위 → `fetchPosts({ nodeId })`

## 검증 체크리스트

- [ ] 학생 로그인 → QnA 탭 → 질문하기 → 제목/내용 입력 후 등록 → 목록에 노출
- [ ] 관리자 로그인 → 커뮤니티 → QnA → 좌측 목록에 해당 질문 노출 → 선택 시 우측에서 상세·답변 작성
- [ ] 관리자 답변 등록 후 학생 앱에서 해당 질문 상세 진입 → 답변 완료·답변 내용 표시
- [ ] 관리자 답변 수정/삭제 시 학생 앱에서 반영 확인
- [ ] 딥링크: `/admin/community/qna/read/:id` → `/admin/community/qna?id=:id` 리다이렉트 후 해당 질문 선택

## 수정 이력 (검증용)

- AdminPostViewSet 클래스 분리 (기존 PostViewSet에 섞여 있던 admin list 제거)
- PostViewSet: 학생 + node_id 없을 때 `created_by=request_student` 필터 및 list 비페이지네이션
- fetchCommunityQuestions: scope=all 시 fetchAdminPosts 사용
- fetchPosts: pageSize 쿼리 지원; 학생 fetchMyQnaQuestions에서 pageSize 200 사용 (백엔드 비페이지 시 무시 가능)
- QnaReadRedirect: `/qna/read/:id` → `/qna?id=:id` 로 이동
