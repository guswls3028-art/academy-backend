# V1.1.1 Release Notes — 기능 안정화 + UX 고도화

**버전:** V1.1.1
**날짜:** 2026-03-15~17
**유형:** 패치 (c 변경 — 인프라 변경 없음)

---

## 주요 변경사항

### 1. 에이전트 모니터 (/dev/agents) — 신규
- 실시간 병렬 에이전트 모니터링 대시보드
- Claude Code hooks → SSE 자동 브리지
- 오피스 플로어 맵 UI (팀 존 기반)
- Office/List 뷰 전환

### 2. 클리닉 UX 전면 재설계
- 탭 이름: 홈→오늘, 운영→클리닉 진행, 예약대상자→예약
- 오늘 탭: 단일 컬럼 액션바 + 타임라인 + 미예약 배너
- 일정 관리: 2존 레이아웃, 생성폼 모달화, 용량 프로그레스 바
- 예약: 단일 컬럼, 접이식 승인, 플로팅 선택 바
- 클리닉 진행: 출석 토글, 인라인 미통과 항목, 학생 상세 오버레이

### 3. 클리닉 진행 콘솔 개편
- 외부 네비게이션 제거 (재시험/과제/학생 페이지로 이동하지 않음)
- 학생별 미통과 시험/과제 인라인 표시 (ClinicTarget 데이터 연동)
- 미통과 없는 학생 → "자율 학습 참여" 라벨
- 클릭 → 우측 드로어에서 점수 상세 + 통과 처리
- "학생 추가" 버튼 → 기존 세션에 학생 등록

### 4. 학생 대시보드 개편
- 다음 일정 카운트다운 (세션/클리닉 구분)
- 오늘 할 일: 과제 미통과 + 재시험 필요 + 클리닉 예약
- 오늘 수업 목록 (시간 표시)
- 중복 "다음 수업" 섹션 제거

### 5. 학생 클리닉 2탭 구조
- 예약 탭 / 내 일정 탭 분리
- 인증패스 카드 제거 (논리적 오류 — 입장 조건이 아님)
- 캘린더 날짜 하루 밀림 수정 (타임존)
- 일정 있는 날짜 dot 표시

### 6. 클리닉 PDF 프리미엄 디자인
- "성적 현황" → "클리닉 대상자 안내"
- 3열 레이아웃, 20px 큰 이름 (벽보 가시성)
- 체크박스 (조교 체크리스트), 메모란, 클리닉 일정 표시
- 점수/개인정보 제외 (공개 게시물 안전)
- 미리보기 모달 → 다운로드

### 7. 영상 인프라 개선 (2026-03-17)
- **좀비 job 정리**: soft-delete 시 모든 active TranscodeJob을 DEAD 처리 (기존: current_job만). Soft-delete는 CASCADE를 트리거하지 않아 RETRY_WAIT job이 좀비로 잔존하던 버그 수정
- **자동 enqueue**: job 완료 후 같은 tenant의 다음 UPLOADED 비디오를 즉시 자동 enqueue (daemon/batch 공통). 수동 개입 불필요
- **R2 원본 3일 보관**: 인코딩 완료 후 즉시 삭제 → 3일 보관 후 `purge_raw_videos` 커맨드로 정리
- **동시 처리 한도 제거**: tenant 2 / global 20 → 사실상 무제한 (9999). 실제 병목은 daemon 인스턴스 수
- **daemon 90분 확장**: `DAEMON_MAX_DURATION_SECONDS` 1800→5400. 30분 초과 영상도 daemon에서 처리
- **이름순 정렬**: Video 기본 정렬 `order, id` → `title, created_at, id` (학생앱/선생앱 공통)

### 8. 영상 UX 개선 (2026-03-17)
- **동적 업로드 슬롯**: 고정 5개 → 1개 시작, 파일 추가 시 자동 확장 (제한 없음)
- **영상 이름 변경**: 상세 페이지에서 제목 클릭 → 인라인 편집 (PATCH API)
- 전체공개영상 → 차시 선택 스킵 (자동 리다이렉트)
- 인코딩 중 영상 "인코딩 중" 배지 표시

---

## 버그 수정

### Critical
- **영상 처리 stuck 버그** — 삭제된 비디오의 좀비 RETRY_WAIT job이 tenant 동시 처리 한도를 차지하여 새 업로드가 영구 UPLOADED 상태에 stuck. 근본 원인: soft-delete가 CASCADE를 트리거하지 않음 + perform_destroy가 current_job만 정리. 3가지 수정으로 완전 해결 (좀비 정리 + 자동 enqueue + 한도 제거)
- 선생님 대상자 등록 400 에러 — enrollment_id→student 자동 resolve 추가
- 학생 클리닉 재예약 차단 — 중복 체크에서 cancelled 제외
- LoginPage 프로모 리다이렉트 무한루프 — 강제 리다이렉트 제거
- React hooks 규칙 위반 (ClinicPage useState 위치)

### High
- 참가자 0명 클리닉 세션 오늘 탭 미표시 — sessionTree 쿼리 추가
- 시험 카드 클릭 → 전부 /admin/lectures — /admin/exams로 수정
- 시험 생성 후 목록 미갱신 — invalidateQueries 추가
- 출석 마킹 시 세션 트리 캐시 미갱신

### Medium
- toggle-back to "booked" 뮤테이션 우회
- approveAll N개 병렬 뮤테이션 추적 불가
- bulkAttend 부분 실패 시 캐시 미갱신
- 학생앱 API 에러 삼킴 (빈 배열 반환)
- 공지 API 에러 삼킴
- 성적표 다운로드 실패 무응답
- 점수 편집모드 기본값 false (셀 편집 불가)
- 드로어 배너 오프셋 미적용

### Low
- 14일 세션 조회 제한 → 60일
- 학생 승인된 예약 취소 버튼 제거 (백엔드 거절)
- 과제 제출 에러 상세 미표시
- 디자인 토큰 하드코딩 정리

---

## 운영 변경

### 학부모 비밀번호 일괄 변경 (2026-03-17)
- Tenant 2: 학부모 17명 → 비밀번호 `1234`
- Tenant 1,3,8,9999: 학부모 243명 → 비밀번호 `0000`
- 변경 범위: `TenantMembership.role='parent'`만 (owner/staff/student/teacher 미변경, 검증 완료)
- E2E 검증: API 8/8 PASS, Playwright 브라우저 2/2 PASS
- 학부모 계정 자동 생성 E2E: 학생 등록 → 아이디=전화번호, 비번=0000 → 카카오톡 알림톡 발송 success=True
- SSOT 문서: `PARENT-ACCOUNT-SYSTEM.md`

## 인프라/운영

- 버전 체커: 새 배포 감지 → "새로운 업데이트가 있습니다" 배너
- 테넌트 에러 페이지: "접근 불가" → "업데이트가 적용되었습니다"
- DevLayout 테넌트 가드 추가 (hakwonplus/9999만 /dev/* 접근)
- AuthContext queryClient.clear() on logout
- NoticeBanner ResizeObserver 높이 추적
- 프로모 로그인 → native <a href> (모달 우회)
- 무중단 배포 원칙 CLAUDE.md §F 명시

---

## 테넌트 격리 감사
- 6항목 점검: 위반 없음
- X-Tenant-Code 헤더, Dev 앱 접근, 에이전트 엔드포인트, queryClient.clear, 클리닉 스코핑, 학생 예약 교차 — 모두 SAFE

---

## 다음 패치 (V1.1.x) TODO
- [ ] `purge_raw_videos` cron 등록 (매일 1회, R2 원본 3일 보관 후 삭제)
- [ ] Video CASCADE 위험: Session/Lecture 삭제 시 soft-delete 우회 → SET_NULL 검토
- [ ] window.confirm 38건 → 커스텀 확인 모달
- [ ] Tailwind 색상 52건 → 디자인 토큰
- [ ] clinic_reason 자동 판정 API
- [ ] ClinicLink.resolved_at 통과 처리 API
- [ ] 재시험/과제 딥링크
- [ ] 반복 일정 생성
- [ ] 동일 시간대 복수 클리닉
- [ ] 학년별 학생앱 노출 필터
- [ ] AttendancePage 백엔드 API 구현
