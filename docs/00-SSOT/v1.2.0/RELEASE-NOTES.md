# V1.2.0 Release Notes — 매치업 신규 도메인 + RDS Proxy + 헥사고날 컷오버

**버전:** V1.2.0
**기간:** 2026-03-18 ~ 2026-04-30 (약 6주)
**유형:** 인프라 변경 (b 변경) — RDS Proxy 도입 + AI worker 모델 사전 캐시 + 다수의 EventBridge 크론
**규모:** backend 753 commit / frontend 663 commit

> 직전 버전 V1.1.1 (2026-03-15~17) 봉인 시점부터의 변경 모음.
> b 변경 트리거: **RDS Proxy 도입**(484 client → 6 backend, 80배 압축) + db 인스턴스 t4g.medium → large.

---

## 1. 신규 도메인 — 매치업 (Matchup)

학원이 보유한 시험지/문항 자료에서 유사 문제를 검색하고, "올해 시험에서 우리 학원이 맞춘 문제"를 마케팅용 PDF로 출력하는 **신규 풀사이클 도메인**.

### 핵심 기능
- **AI 매치업 V1**: 유사 문제 추천 시스템 (`MatchupDocument` / `MatchupProblem`)
- **AI 매치업 V2**: 시험 인덱싱 + Q&A 연동 + 유사검색 출처 정보
- **자동 분리(Segmentation)**: OCR 룰 기반 anchor (번호 1~60 + 서술형 regex + cross-page 검증) — YOLO 재학습 회피 (Plan F)
- **YOLO V1→V2** 학습 이력 (공통 인프라)
- **CLIP 이미지 임베딩 ensemble** — 워커 자동 페이지 캐시. T1+T2 1156 problem image_embedding 100% 백필
- **Cross-encoder reranker** (BAAI/bge-reranker-v2-m3) — 환경변수 토글, 기본 OFF
- **Phase 2 휴리스틱 reranker** + 텍스트 정제 + format 메타
- **카메라 사진 OCR 전처리** — Vision dimension 한도(75M px) 초과 silent reject 해결 (byte+pixel 이중 체크)
- **워터마크 strip** + 페이지폴백 false positive 차단 + 적중률 3단계 분리
- **수동 크롭 도구 (ManualCropModal)** — 박스 핸들/이동 + 최소 크기 가드 + 카테고리 자동 추출
- **Ctrl+V paste-as-problem** — 매뉴얼 크롭 모달에 이미지 붙여넣기 → 신규 problem 인덱싱 워커 잡
- **시험지 적중률 PDF 보고서** (학원 마케팅용) — 자동/수동 모드. timeout 5분 명시
- **HitReportEditor (큐레이션 적중 보고서 작성기)** — 별표 찜 + 빈 후보 안내 + 자동 저장 + 선택 카운트 배지 + 자동 hit-report-draft 로드
- **storage-as-canonical 통합** — `MatchupDocument.inventory_file` FK NOT NULL. 승격(promotion) UI/API. 원본 PDF/이미지 미리보기 endpoint
- **카테고리 격리 강화** + 시험지 역할(teacher/exam) 기반 매치 분리
- **다중 업로드 (B2)** — 라디오 + 자동 split + 부분 수용 + ETA + entry 상태
- **표지/목차 가드** + `is_non_question_page` 강화 + dispatcher `skip_page`
- **box-merge / over-extraction 의심 검수 배지** + ProblemGrid 가이드 띠
- **fix_problem_numbers** management command (분리 결과 실제 번호 매핑)
- **backfill_embeddings** management command

### 운영 적용 결과
- 풀사이클 검증 (Tenant 2 tchul, 운영 16 doc/384 problem)
- self_start 비율 64.7% → 88.7%
- 시험지 sim 100% S+M (T2 doc#148 4분할 가로띠 컷 → DB 직접 text/embedding update, MiniLM-L12-v2 로컬 재임베딩 패턴 확립)
- T2 28 doc 누적 -63.4% (2381→872) 운영 UI E2E 3/3 PASS
- 페이지 sub-crop: anchor 1~4 / 5+ over-extraction 페이지 통째 정책

### Phase 2 ML 의존성
- 컨테이너에서 분리 (AI 워커 전용)
- 모델 다운사이즈 (cross-encoder base)

---

## 2. AI / 외부 API 안정화

### AI Quota Enforcement (P0-A)
- `AIUsageModel` 신규 — 테넌트별 AI 호출량/금액 기록
- 5종 호출처 인터셉트 (matchup analysis, OMR, OCR 등)
- 한도 초과 시 호출 차단

### External API Circuit Breaker (P0-B)
- 4개 서비스 적용: Solapi(알림톡/SMS), Toss(결제), OpenAI, Vision OCR
- 연속 실패 시 일정 시간 호출 차단 → 외부 장애가 워커를 wedge 시키지 않음
- check_dev_alerts에 `circuit_open` 룰 추가

### Worker Heartbeat (P1-D)
- AI/Messaging 워커 heartbeat 발행
- check_dev_alerts에 `stale_workers` 룰 추가 (heartbeat 끊긴 워커 자동 알림)

### Disaster Recovery Runbook (P1-A)
- RTO/RPO 명시
- 복구 절차 + 검증 체크리스트
- `docs/00-SSOT/v1.1.1/RUNBOOK-*` 시리즈 보강

### AI Worker 안정화
- **CLIP + MiniLM 가중치 docker 이미지 사전 캐시** (cold start 제거)
- **Dockerfile 레이어 재배치** — 모델 캐시를 코드 위로
- **CLIP 배치 + 60분 timeout 시 hard-exit** — 85% wedge 사고 영구 방어
- `mark_running` 중복 실행 차단 + SQS 반환값 검증 + tmp 누수 self-clean

---

## 3. 인프라 변경 (b 세그먼트 변경 트리거)

### RDS Proxy 도입 (2026-04-29)
- **사고**: connection 만석 (829/830)
- **응급**: db.t4g.medium → large 인스턴스 업그레이드
- **항구**: RDS Proxy 도입
- **효과**: 484 client → 6 backend connection으로 80배 압축. 워커 무한 확장 가능
- API CONN_MAX_AGE 60 → 5 (connection leak 영구 방어)
- 워커 CONN_MAX_AGE 60 → 0

### EventBridge 크론 다수 추가
- `cleanup_orphan_video_storage` weekly (R2 137GB 누적 정리)
- `cleanup_e2e_residue` weekly (Tenant 1 운영 잔재 656 rows + 픽스처 보호 + ALLOWED_TENANTS 가드)
- 차시 시험/과제 자동 마감 daily cron (프론트 useEffect → 백엔드 이전)
- 소프트삭제 학생 30일 / 영상 180일 자동 파기
- video reconcile/scan 빈도 1시간으로 하향
- detect_stuck_videos EventBridge 등록

### CI/CD 정비
- ECR cleanup 자동화 — 배포 후 old sha- 이미지 정리 (manifest-aware script로 재구현)
- ECR weekly cron silent-fail 패치 (commit 27b5fecd) + DOCKER_BUILD_SUMMARY=false
- 마이그레이션 step InService 인스턴스 없을 때 90초 retry
- 마이그레이션이 구 이미지에서 실행되던 설계 결함 수정
- multi-commit push detect-changes 누락 수정
- detect-changes에서 multi-commit push 누락
- workflow provenance 누적 차단 + GH artifacts 1.59 GB 정리

### Lambda
- Lambda 큐 기본값 V1 SSOT 정렬

### 신규 테넌트
- **DNB Academy** (dnbacademy.co.kr) — Tenant 9

---

## 4. 헥사고날 컷오버 (대규모 리팩토링)

`backend/academy/` (헥사고날 도메인/포트/어댑터/워커) ↔ `backend/apps/` (Django CRUD/HTTP/워커 entry) 공존 정책 명문화.

### SSOT 신설
- `docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md` (필독)

### 단계적 이전
- **PR-1 (2026-04-28)**: video transcoder 9 파일 → `academy/adapters/video/`. reverse coupling 7건 해소. 운영 ops job sha-64c99d17 SUCCEEDED
- **PR-2 (2026-04-28)**: AI 파이프라인 → `academy/adapters/ai` + `use_cases/ai/pipelines`
- **PR-3 (2026-04-28)**: video/messaging 도메인 모델 → `apps/domains/`. db_table 그대로 유지. 운영 messaging/video 모두 PASS
- support↔worker 의존 감사 — cross-worker 1 cycle (ai_worker↔omr) 해소. omr → `ai_worker.ai.omr` 이관
- 평가 5도메인 audit (exams/submissions/results/homework/homework_results) — `results=SEALED exam-only` 명문화. HomeworkScore 코드를 `homework_results` 도메인으로 이관

### 프로젝트 구조 대규모 정리 (commit 7845d9af, 2026-04-13)
- 데드코드 제거
- 헥사고날 통합
- 도메인 표준화

### 루트 산출물 정리
- 스캔/PDF/모델파일/세션 백로그 → `C:\academy\_artifacts/` (gitignore)

---

## 5. OMR 자동채점 시스템

### v15.2 인식마크 재설계
- 얇은 ㄱ자 브래킷 + 로컬 앵커
- 실사용 인식률 11% → 100% 복구
- v8 디자인 전면 개선 (상품 수준 업그레이드)
- 기본 로고 책 아이콘 / 성명란 밑줄 1개 통합

### SSOT 재설계
- document model + reportlab PDF + HTML preview
- v14 호환 + meta 동기화 + engine mask 가독성 개선
- marker_detector 코너 배정 버그 2건 + 실전 시뮬 테스트
- timing mark 한국식 바코드 스트립으로 업그레이드

### 수동 매칭·수정 UI V1.3
- 3패널 워크스페이스
- 학생 검색 (student picker endpoint + enrollment_id 검증)
- dirty 가드
- BBox 오버레이 (v10.1 bubble_rects + per-question rect)
- 폐기/duplicate guard
- longer scan TTL
- alignment failure reason + dynamic version meta

### Dead anchor / centroid offset 버그 수정
- Phase 1 구현 완료, SSOT 정합성 확보

---

## 6. 커뮤니티 전면 개편

### 보안 (commit 419a672a/50cee2a2)
- MIME 검증
- Sanitizer (HTML sanitization)
- 파일명 sanitization
- 학부모 권한 상승 회귀 + retrieve draft 노출 fix

### 모델 / API
- `is_pinned`, `status`, `published_at` 필드
- counts 단일 집계
- author_role 표시 (학부모 라벨)
- PostThreadView/4훅 통합
- BlockType 완전 제거 + 자료실 일방향 정책 명문화
- ScopeNode signal — created 제한 제거, 기존 데이터 자동 복구

### 학부모 글 작성 (Option 2 hybrid)
- Parent 모델 활용
- `author_role=parent` 분기
- 답변 알림 학부모 폰만
- 회귀 17/18 PASS

### 답변 알림톡
- `qna_answered` / `counsel_answered` 트리거
- TYPE_SCORE 재사용으로 신규 검수 회피
- AutoSendConfig 토글로 학원별 ON
- 기존 테넌트에 신규 community trigger 일괄 provision

### UIUX
- 상담/QnA 라벨·상태·카테고리·CTA·학생패널 4화면 정합성
- QnA/Counsel scope 바 제거 (`scope='all'` 노이즈 인디케이터)
- chip CSS co-locate 버그 수정
- legacy dead 정리

### 69 테스트 추가

---

## 7. 보강 사이클 SSOT (Clinic Remediation)

### 서비스 신규
- `ClinicResolution` / `Remediation` / `Trigger` 서비스

### 어드민 / 학생 진입
- 어드민 3호출처 통합
- 학생 ExamResult CTA
- ClinicTargetSelectModal 일괄선택

### 정책
- **clinic remediation 재정렬**: 예약 ≠ 해소, 시험/과제 통과 = 해소
- ClinicLink 시험별 개별 추적 (`source_type` / `source_id`) — 근본 보강
- 재불합격 시 `cycle_no` 증가 생성 — resolved 이후 재등장 보장
- 시험↔클리닉 드리프트 해소 + 해소/이력 일관성 전면 개선
- change_booking ClinicLink 이중 resolve 버그 수정
- 시험 자동채점 전면 장애 수정 + 학생앱 데이터 정합성 복원
- 진입점 pipeline dispatch + legacy backfill 도구
- 성취 SSOT 유틸 + clinic_reason API
- ClinicLink.resolved_at 통과 처리 API

---

## 8. 결제 / 회비 / 자동결제

### 자동결제(Phase D) + Toss 웹훅 완성
- billing go-live checklist (사용자 직접 액션 4건 정리)
- BillingKey active partial unique 제약
- webhook TOCTOU 방어 + 환불 상태 역전 방어
- fees lifecycle 단위테스트 12개

### 회비
- 마감월 정책 일관 적용 + WorkType 보호 메시지
- get_dashboard_stats 상대 import 경로 교정

---

## 9. 영상 / 인코딩

### PROCTORED_CLASS
- **서버 세션 자동 발급** (진짜 token 사용)
- 관리자 설정 배속 무시되는 버그 수정

### 인코딩 / 화질
- 영상 인코딩 화질 상향 (강의 영상 기준 선명도 최적화)
- 전체공개영상 도메인 모델 분리 — 강의 엔티티 → 접근 정책(visibility)
- HLS 화질 선택 + 자동 재연결
- 영상 stats 엔드포인트 (`GET /student/video/me/stats/`)
- stats 엔드포인트 세션 미연결 영상 500 fix
- 영상 인코딩 자동 복구 하드닝 (RETRY_WAIT 무한 방치 방지)
- 워커 네이밍 불일치 + detect_stuck_videos EventBridge

### 워커 빌드/배포
- video worker Batch deploy file:// 방식으로 shell escaping 회피
- batch job def 배포 실패 수정
- generate_presigned_download_url import 수정

### 영상 인코딩 알림톡
- Solapi 카카오 검수 승인 대기 (외부 절차)

---

## 10. 선생앱 (Teacher Mobile App)

### Phase 1+2+3 완료 + UX 고도화
- Push 알림 + BFF 엔드포인트
- PC 1:1 매칭
- Today 재구성 (8타일 / vanity KPI 제거)
- 검수/실패 카운트 위젯
- routes SSOT 분리
- teacher-dashboard-counts endpoint = `video_failed`(30일 윈도우)만 (score/matchup 폐기 — 모델 불일치/의미 오해)

### 시험/과제
- 상태 뱃지 + 정렬

### 헤더 위젯
- 4종 P1·P2 개선 + 후속 P3-4/5/6/7/8

---

## 11. 학생앱 / 학부모

### 학생 대시보드
- 정보 우선순위 재배치
- 학부모 자녀 스위처 (currentId 동기화 fix — 첫 렌더 활성 칩 미표시)
- 자녀 전환 시 아바타 깜빡임 + 정보 중복 + 빈 학원문의 fix
- 다음 일정 카운트다운 (세션/클리닉)
- 오늘 할 일 (과제 미통과 + 재시험 필요 + 클리닉 예약)
- 공지 NEW 뱃지 + 새 답변 정확 라우팅
- 다크모드/회귀 spec
- topbar 자녀 dropdown 제거 + TodoRow tap highlight

### 학생 차시 허브
- 상태 칩

### 학생 본 알림 추적
- 정합 (학부모는 별도 알림)

### 학부모 신규 계정 비밀번호 변경 강제
- + SQL 안전성 강화

---

## 12. Badge / Icon SSOT 표준화 (전역 규약)

### 신규 SSOT
- `<Badge>` from `@/shared/ui/ds`
- `ICON.*` 토큰 (xs/sm/md/lg/xl)
- `ICON_FOR_BUTTON.*` / `ICON_FOR_BADGE.*`
- 근거 토큰: `frontend/src/styles/design-system/density/size.css`

### 마이그레이션
- 7 wrapper + raw 22건 → `<Badge>` SSOT
- raw `<span ds-badge>` 36건 → SSOT (백로그 cleanup commit 2a9d94d3)
- 도메인 Badge 마이그레이션 (scores/submissions/exams/homework/community/teacher)
- StudentsDetailOverlay text-[11px] 강제 축소 정상화

### 가드
- ESLint 36건 잔존 warn (raw size={10/12/14} 신규 금지)
- 인라인 스타일 baseline 동결 + 신규 차단 가드 (R-11)

---

## 13. 보안 정밀검사

### C-1~C-4 학생 권한 누출 차단
- submissions / wrong-notes / ppt / exam
- 24 케이스 회귀 테스트 추가 (ALL GREEN)

### C-5 landing-stats 보안
- 9 케이스 회귀 가드

### JWT
- `tenant_id` claim 교차 검증 추가 — 헤더 변조 방어
- 비밀번호 변경 시 기존 JWT 무효화
- 로그인 brute force 방어

### Tenant
- submissions/results Enrollment·Exam·Homework `id__in` 조회에 tenant 강제

### 학부모
- 학부모 권한 상승 회귀 + retrieve draft 노출 fix
- 학부모 write 차단 (커뮤니티 base)

### PII / 응답
- PII guard
- `parse_bool` (안전한 boolean 파싱)
- 500 leak fix

### 법적 고지
- 학원 정보 미입력 시 학부모/학원장에게 fallback 경고 노출
- 운영 E2E 2건

---

## 14. E2E Hardening

### 정량 효과
- `waitForTimeout` 845 → 189 (-78%)
- strictBrowser 24
- 80 spec / 544 test
- E2E_STRICT 회귀 진단 (1차) — 38건 신규 fail 발견 (CORS multi-tenant 8+ / lazyWithRetry 5 / AxiosTimeout 4 / ERR_FAILED 16)
- about:srcdoc IGNORE 추가, workflow report 회귀

### 운영 잔재 정리
- `cleanup_e2e_residue` management command + 괄호 없는 E2E 지문 패턴 추가
- 656 rows 정리 + 주간 cron + 픽스처 보호 + ALLOWED_TENANTS 가드 (Tenant 1만)

### Workflow
- timeout 45 → 90 → 120 분
- retries 2 → 1 (strict baseline 진단용)

---

## 15. 성능 (N+1 제거)

| 대상 | 효과 |
|------|------|
| Serializer N+1 4건 | teacher/student/question/clinic-participant |
| StaffListSerializer N+1 | role 룩업 1회 prefetch |
| ExamSubmissionsListView | 400 → 2 쿼리 (R-4) |
| Section assignment | bulk_create None 세션 가드 |
| Matchup ProblemListView | image_url 포함 (프론트 N+1 presign 제거) |
| Matchup find_similar | numpy 벡터화 + OCR R2 영구 캐시 |
| Landing stats | KPI 인박스 백엔드 집계 (results/videos) |

---

## 16. 도구 / PPT / 타이머 / OMR

### PPT 흑백반전
- 텍스트 레이어 0 PDF 페이지 fallback
- DONE-without-result 응답 무한 폴링 차단
- `status_for_exception` silent-DONE error_message 노출
- 운영 3종 검증

### 도구 4탭 실사용 리뷰 (PPT/OMR/클리닉/타이머)
- P0 4건 (업로드 silent reject · iframe 동기화)
- P1 5건 (clamp 안내 · 자동 갱신 · 진행률 라벨 · 모드 전환 confirm · 파싱 실패 가이드)
- 운영 E2E 10/10 PASS (commit 2d891811)

### 타이머
- EXE → ZIP 전환
- R2 presigned URL 다운로드 API
- 테넌트 매핑 SSOT JSON (`backend/.../timer_tenants.json` 1곳 → backend + build/upload 자동 반영)

### PDF 문항 분할
- POST `/exams/pdf-extract/` 신규
- PyMuPDF Dockerfile 추가
- common.txt도 COPY 추가

### OMR PDF 폰트
- .ttc subfontIndex 지원 + nanum fallback + bold cascade
- Bold 미등록 시 Regular로 대체

---

## 17. 메시징 / 알림톡

### SMS/알림톡 분리 (2026-04-01)
- modal redesign 완료

### 알림톡 전용 피벗 + 트리거 명 정합
- 자동발송 미구현 트리거 admin UI 가시성

### 출석/결석 알림톡
- 실제 도착시간 변수 + ITEM_LIST 시간 모드 설정

### 알림톡 오너 테넌트 fallback
- 전 테넌트 알림톡 발송 가능

---

## 18. 운영 콘솔 / 대시보드

### /dev 운영 콘솔 V2 (2026-03-15~17 + 후속)
- 대시보드 / 테넌트 ops / 감사 로그 / 자동화
- 검색 / 자동화 / 임퍼소네이션 5 spec
- /dev 알림 크론 + Slack webhook 설정 헬퍼
- DevLayout 테넌트 가드 (hakwonplus/9999만 /dev/* 접근)

### 헤더 위젯
- 4종 P1·P2 개선 + P3-4/5/6/7/8 후속

### 사이드바
- 짧은 화면/배율에서 하단 메뉴 클릭 불가 수정
- 스크롤바 시각 숨김 (스크롤 동작은 유지)

### 어드민 UIUX 실사용 감사
- P0/P1 — 공지 stub · 식별 · 중복 · 로딩 · destructive 위계

---

## 19. 도메인 실사용 리뷰 (다회 진행)

### 시험·성적·과제 (commit e2c5c8ba)
- 3역할 리뷰 + P0/P1 12건 (학부모 가드 / 입력 손실 / prompt 제거 / 저장 토스트 / 미제출 CTA / 0명 경고 / 메시지 우선순위 / 시간순 / 라벨 / PDF 브랜딩 / OMR 진행률)

### 6도메인 (영상 / 출결·차시 / 회비 / 메시징 / 계정 / 학생앱)
- P0 16건 + P1 4건
- backend daa4d667 / frontend 6a91dd0f+24fbaf74
- 운영 E2E 통과 (GH Actions PASS, 신규 endpoint 200, SQS 0)

---

## 20. 주요 버그 수정

### Critical
- **시험 자동채점 전면 장애** + 학생앱 데이터 정합성 복원
- **연결 만석 사고** (829/830) — RDS Proxy 도입으로 영구 해결
- **AI worker 85% wedge** — CLIP 60min hard-exit 도입
- 매치업 콜백 ValueError — 워커 INSTALLED_APPS inventory/matchup 누락 (630be09f) + AIResultModel.payload로 28 doc 무손실 복구
- 매치업 cross-matches 500 + self-doc trap

### High
- progress pipeline 동기 dispatch — `on_commit` 미실행 문제
- 점수 직접 입력 시 progress pipeline dispatch — ClinicLink 생성 보장
- 시험 자동채점 OMR 정합성
- ranking 석차=1차 정책 (initial_snapshot 기반 불변 점수)
- billing webhook TOCTOU + BillingKey active partial unique
- community 댓글 멱등 가드 (중복 호출로 comment_count 과감소 차단)

### Medium
- 영상 PROCTORED_CLASS 관리자 설정 배속 무시
- 영상 stats 엔드포인트 세션 미연결 500
- lectures 제목 중복 500 → 400 사용자 메시지
- SessionProgress exam_meta datetime 직렬화
- ClinicLink 재불합격 시 cycle_no 증가
- ProgressPolicy exam/homework 범위 1차시부터 적용
- ClinicLink 시험별 개별 추적 (source_type/source_id)
- matchup R2 orphan + race + silent partial fail (5건)
- matchup-empty CTA mental model 정합
- parent-switcher currentId 첫 렌더
- student-dashboard 자녀 전환 깜빡임
- sidebar 짧은 화면/배율 하단 메뉴 클릭 불가
- matchup splitter 정답 단독 + 형 필수 (commit 7571b71d)

### Low
- 매뉴얼 크롭 dispatch 결과를 problem.meta에 노출
- 도구 4탭 P1 5건
- D-Day 기능 코드 제거 (commit 1254b446)
- 매치업 carbon — 좌측 자료 트리 별도 스크롤
- 사용자 알림 toast 명칭 통일 ("작업박스")
- ProfilePage 직책 표기 SSOT 정합 + 결제 페이지 안내 문구

---

## 21. 데이터 정리 / 운영 변경

### 전수조사 검증 (2건)
- V1.1.1 전수조사 — race condition / tenant isolation / state machine / dead code 정리
- 84f822ee — 테넌트 격리 / import 오류 / serializer 강화

### Cleanup
- R2 137GB orphan 정리 + `cleanup_orphan_video_storage` 커맨드 + 주간 크론
- ECR 17.4GB + Batch 104 revisions 정리 + workflow provenance 누적 차단
- ECR 51.81 GiB + GH artifacts 1.59 GB (2026-04-27)
- Tenant 1 운영 잔재 656 rows 정리 + 주간 크론 + 픽스처 보호 + ALLOWED_TENANTS 가드

### 백로그 일괄 정리 (2026-04-28)
- 매치업 splitter P1 + Badge SSOT 36→0 + D-Day 폐기
- 운영 E2E 3/3 PASS

### 잔여 리스크 점검 (2026-04-29)
- 10개 영역 리스크 점검
- 자율 처리 P0 3건 (PII 마스킹 / fees 단위테스트 12개 / 법적 고지 fallback 경고)
- 미해결 백로그 P0 3 + P1 4 → `_artifacts/sessions/SESSION-BACKLOG-2026-04-29-risk-audit.md`

---

## 22. 검증 (V1.2.0 봉인 시점)

### E2E
- 80 spec / 544 test
- waitForTimeout 845→189 (-78%)
- strictBrowser 24
- 운영 E2E 다수 PASS (매치업 16 doc / 도구 10/10 / 어드민 통합 등)
- E2E_STRICT 1차 진단 완료 (별도 메모리)

### 워커 연결
- API: healthz=200, health=200
- Messaging worker: SQS=0, DLQ=0
- AI worker: InService, SQS=0, DLQ=0
- Video worker: 운영 ops job sha-64c99d17 SUCCEEDED

### 보안 회귀
- C-1~C-4·H-4: 24 cases all green
- C-5 landing-stats: 9 cases

### 인프라
- RDS Proxy 정상 동작 (484 client → 6 backend)
- weekly cron 10종 동작 확인
- ECR/R2/GH artifacts 정리 정상

---

## 23. 다음 패치 (V1.2.x) TODO

### 미해결 P0/P1 (2026-04-29 리스크 점검에서 도출)
- 잔여 P0 3 + P1 4 → `_artifacts/sessions/SESSION-BACKLOG-2026-04-29-risk-audit.md`

### E2E_STRICT 잔여 fix (2026-04-29 1차 진단)
- CORS multi-tenant 8+ 건
- lazyWithRetry 5 건
- AxiosTimeout 4 건
- ERR_FAILED 16 건
- → 정상화 후 strictBrowser 재플립

### 백로그 (V1.1.x 이연)
- Video CASCADE 위험: Session/Lecture 삭제 시 soft-delete 우회 → SET_NULL 검토
- Student phone uniqueness DB 제약 추가
- Student self-edit phone validation/dedup 강화
- OTP rate limiting 추가
- window.confirm 38건 → 커스텀 확인 모달
- Tailwind 색상 52건 → 디자인 토큰
- 재시험/과제 딥링크
- 반복 일정 생성
- 동일 시간대 복수 클리닉
- 학년별 학생앱 노출 필터
- AttendancePage 백엔드 API 구현

### 매치업
- 페이지 sub-crop 백로그 closed (V1.2 내 처리됨)
- splitter 정답 단독 + 형 필수 closed
- 강등(demotion) UI/API → V2 이연 (storage-matchup 통합 후속)

### 헥사고날 컷오버 후속
- 전수조사 후속 잔여 R-4/R-6/R-10/R-11/R-14 큰 리팩터 백로그
- R-11 4-29 5,442건 (+3,652)

### 영상 인코딩 알림톡
- Solapi 카카오 검수 승인 대기 (외부 절차)
