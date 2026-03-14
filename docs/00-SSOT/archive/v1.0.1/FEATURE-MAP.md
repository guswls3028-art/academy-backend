# V1.0.1 Feature Map — 전체 기능 현황

**Snapshot Date:** 2026-03-11

---

## 1. Admin 기능 (관리자/원장)

### 1.1 대시보드 (`/admin/dashboard`)
- [x] 학생 수, 강의 수, 수업 수 KPI 카드
- [x] 최근 공지 요약
- [x] 빠른 메뉴 네비게이션

### 1.2 학생 관리 (`/admin/students`)
- [x] 학생 목록 (검색, 필터)
- [x] 학생 상세 오버레이 (프로필, 수강, 성적, 출결)
- [x] 학생 등록 (수동 + 엑셀 업로드)
- [x] 학생 삭제/복원
- [x] 가입 요청 관리 (`/admin/students/requests`)

### 1.3 강의 관리 (`/admin/lectures`)
- [x] 강의 CRUD (활성/종료 분리)
- [x] 차시 관리 (순서, 날짜, 설정)
- [x] 차시별 출결 (`/attendance`)
- [x] 차시별 성적 입력 (`/scores`) — 시험+과제 통합
- [x] 차시별 시험 관리 (`/exams`)
- [x] 차시별 과제 관리 (`/assignments`)
- [x] 차시별 영상 관리 (`/videos`)
- [x] D-Day 관리 (`/admin/lectures/:id/ddays`)
- [x] 학생 배정 관리 (`/admin/lectures/:id/students`)

### 1.4 시험 (`/admin/exams`)
- [x] 시험 탐색기 (강의·차시 트리)
- [x] 시험 생성 (커스텀, 템플릿, OMR)
- [x] 시험 정책 설정 (합격점, 재응시)
- [x] 정답 키 등록
- [x] 수강생 대상자 관리
- [ ] 재계산 기능 (스텁)

### 1.5 성적 (`/admin/results`)
- [x] 성적 탐색기 (강의·차시 트리)
- [x] 성적 입력/조회
- [x] 시험 분석 패널

### 1.6 영상 (`/admin/videos`)
- [x] 영상 탐색기 (전체공개 + 강의별)
- [x] 영상 업로드 (SQS → AWS Batch 비동기)
- [x] 폴더 생성/삭제
- [x] 영상 재시도/삭제
- [x] 영상 상세 페이지
- [x] 영상 감사 로그 (`/admin/videos/audit`)

### 1.7 저장소 (`/admin/storage`)
- [x] 내 저장소 / 학생 저장소 분리
- [x] 폴더 CRUD, 파일 업로드/삭제
- [x] 파일 이동 (드래그 or 모달)

### 1.8 교재 (`/admin/materials`)
- [x] 교재 라우트 (MaterialsRoutes)
- [x] OMR 시트 관리
- [x] 시트 제출물 관리

### 1.9 메시지 (`/admin/message`)
- [x] 메시지 발송 (문자 SMS/LMS)
- [x] 자동 발송 설정 (신규 V1.0.1)
- [x] 템플릿 블록 관리
- [x] 발송 이력

### 1.10 커뮤니티 (`/admin/community`)
- [x] 게시판 관리
- [x] 공지사항 관리
- [x] QnA 관리 (수신함)
- [x] 상담 관리
- [x] 자료실 관리
- [x] 커뮤니티 설정

### 1.11 클리닉 (`/admin/clinic`)
- [x] 클리닉 홈 (자동 승인 설정)
- [x] 운영 콘솔
- [x] 클리닉 메시지 설정 (신규 V1.0.1)

### 1.12 직원 관리 (`/admin/staff`)
- [x] 직원 목록/생성
- [x] 근무 기록, 경비, 월 잠금
- [x] 직원 상세 (설정, 근무, 경비, 수당)
- [x] 직원 설정 페이지 (신규 V1.0.1)

### 1.13 설정 (`/admin/settings`)
- [x] 프로필 설정
- [x] 기관 설정
- [x] 외관 설정 (테마)

---

## 2. Student 기능 (학생 앱)

### 2.1 대시보드 (`/student/dashboard`)
- [x] 오늘 일정 카드
- [x] 공지사항 위젯
- [x] 빠른 메뉴 (영상, 시험, 커뮤니티, 제출, 성적, 인벤토리, 클리닉)
- [x] 학원 문의 정보

### 2.2 영상 (`/student/video`)
- [x] 코스 카드 목록 (전체공개 + 강의별)
- [x] 코스 상세 (세션별 영상)
- [x] 비디오 플레이어 (HLS, 전체화면, PiP)
- [ ] 진행률 표시 (하드코딩 0)

### 2.3 일정 (`/student/sessions`)
- [x] 세션 목록 (달력 포함)
- [x] 세션 상세

### 2.4 시험 (`/student/exams`)
- [x] 시험 목록 (마감 기반 UI 변형)
- [x] 시험 상세 / 응시
- [x] 시험 제출
- [x] 시험 결과

### 2.5 성적 (`/student/grades`)
- [x] 시험 결과 목록
- [x] 과제 이력
- [x] 상세 성적

### 2.6 제출 (`/student/submit`)
- [x] 제출 허브
- [x] 성적 제출
- [x] 과제 제출

### 2.7 인벤토리 (`/student/inventory`)
- [x] 내 인벤토리 (저장소)

### 2.8 커뮤니티 (`/student/community`)
- [x] 공지/게시판/자료실/QnA/상담 탭

### 2.9 공지 (`/student/notices`)
- [x] 전체/강의/차시 공지 탭
- [x] 공지 상세

### 2.10 알림 (`/student/notifications`)
- [x] 알림 목록
- [x] 알림 카운트 배지

### 2.11 클리닉 (`/student/clinic`)
- [x] 패스카드 (실시간 색상)
- [x] 예약 캘린더
- [x] 예약 신청/취소

### 2.12 프로필 (`/student/profile`)
- [x] 개인정보 조회/수정
- [x] 비밀번호 변경

### 2.13 기타
- [ ] 출결 현황 — 플레이스홀더 (API 미구현)
- [x] 설정 페이지
- [x] 클리닉 인증 패스

---

## 3. Backend Domains

| Domain | App | API Status |
|--------|-----|------------|
| 인증/권한 | core (auth, tenants, permissions) | Production |
| 학생 | students, parents, enrollment | Production |
| 강의 | lectures | Production |
| 시험 | exams, submissions | Production |
| 과제 | homework, homework_results | Production |
| 성적 | results | Production |
| 영상 | video (worker: batch) | Production |
| 출결 | attendance | Production |
| 클리닉 | clinic | Production |
| 커뮤니티 | community | Production |
| 메시지 | messaging (SQS worker) | Production |
| 저장소 | inventory | Production |
| 교재 | assets | Production |
| 일정 | schedule | Production |
| 직원 | staffs, teachers | Production |
| AI | ai (SQS worker) | Production |
| 진행률 | progress | Partial |

---

## 4. 미구현 / 향후 과제 (V1.0.2+)

1. 학생 출결 API + 프론트엔드
2. 영상 진행률 계산 시스템
3. 직원 수당 API + 프론트엔드
4. 프로모 사이트 데모/문의 API
5. 시험 재계산 프론트엔드 완성
6. 영상/성적 알림 카운트 백엔드
