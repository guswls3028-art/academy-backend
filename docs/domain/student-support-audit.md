# 학생 지원 대리보기·활동 감사 계약

**상태:** Active
**최종 확인:** 2026-08-29 KST
**정본 구현:** `apps/domains/students/views/support_views.py`,
`apps/domains/students/services/activity.py`, `apps/domains/students/models.py`,
`apps/core/authentication.py`

## 목적과 권한

관리자·직원이 학생 문의를 처리할 때 학생 비밀번호로 다시 로그인하지 않고 실제
학생 앱을 볼 수 있어야 한다. `POST /api/v1/students/<student_id>/support-session/`
은 요청 테넌트의 활성 학생과 `TenantResolvedAndStaff` 권한이 모두 확인된 경우에만
대리보기 세션을 만든다. 다른 테넌트 학생을 추정하거나 찾는 fallback은 없다.

관리자 영상 상세의 학생 시청 현황도 같은 지원 세션을 재사용한다. 영상 통계
`GET /api/v1/media/videos/<video_id>/stats/`는 각 tenant-scoped 수강 행에
`student_id`를 함께 반환하고, 프런트는 이 값으로만 학생 화면 보기를 연다. 이름,
전화번호 또는 수강 ID를 학생 ID로 추정하거나 별도 학생 검색으로 보정하지 않는다.

대리보기 토큰은 다음 경계를 가진다.

- 15분짜리 access token만 발급하며 refresh token은 만들지 않는다.
- `support_preview`, `impersonated_by`, `support_session_id`,
  `support_student_id` claim으로 일반 학생 세션과 구분한다.
- 매 요청마다 `impersonated_by` 교직원의 현재 활성 staff membership을 다시
  확인한다. 권한이 회수되면 아직 만료 전인 토큰도 즉시 거부한다.
- 발급 세션은 `StudentSupportSession`에 학생·교직원·만료·종료 시각을 저장한다.
  JWT의 세션 UUID와 활성 DB 행이 함께 일치해야 하므로 브라우저 저장소에서 토큰만
  복사하거나 이미 종료한 세션을 다시 사용할 수 없다.
- 시작은 `OpsAuditLog(action="student_support_view.start")`에 교직원 actor와 학생
  target을 함께 남긴다.
- 학생 팝업의 `POST /api/v1/students/me/support-session/end/`와 교직원 원창의
  `POST /api/v1/students/<student_id>/support-sessions/<session_id>/end/`는 같은
  세션을 멱등적으로 종료한다. 종료 시 `student_support_view.end`를 남긴다.
- 토큰 만료·명시 종료·계정 비활성화·테넌트 불일치 시 학생 API 권한이 닫힌다.
  세션 행은 지원 감사 수명주기 증거이므로 종료 뒤에도 유지하며 학생 학습 기록으로
  합치거나 로그인 기록으로 변환하지 않는다.

프런트 팝업 전달·저장 경계는 academy-frontend
`docs/STUDENT-PARENT-APP-CONTRACT.md`가 소유한다.

## 학생 로그인과 화면 활동

학생 로그인 증거는 실제 아이디·비밀번호 검증이 성공하고 학생 프로필 및 현재
테넌트의 활성 student membership이 확인된 때에만
`student_activity.login`으로 기록한다. 대리보기 토큰 발급과 사용은 이 로그인
경로를 통과하지 않으므로 학생 로그인으로 기록하지 않는다.

학생 앱은 아래 허용 목록에 있는 화면을 성공적으로 연 뒤
`POST /api/v1/students/me/activity/`로 서버 수신 시각을 남긴다.

| 분류 | 대표 화면 증거 |
|------|----------------|
| `home` | 학생 홈, 차시 목록·상세 |
| `homework` | 과제 제출 화면 열기 |
| `video` | 영상 홈·차시·플레이어 열기 |
| `exam` | 시험 목록·상세·제출 화면 열기 |
| `result` | 시험 결과·성적 화면 열기 |
| `attendance`, `clinic`, `notice` | 출결·클리닉·공지 화면 열기 |
| `profile`, `fee`, `guide` | 프로필·수납·가이드 화면 열기 |

이 기록은 화면을 열었다는 서버 수신 증거다. 영상 완주, 과제 제출 완료, 시험
응시 완료처럼 각 도메인의 별도 상태 전이를 대신하지 않으며 분쟁 확인 시 해당
정본 데이터와 함께 판단한다. 기록 실패는 학습 화면을 막지 않는다.

과제 선택은 `POST .../homework-open/`, 영상 재생 URL 발급은 명시적 playback
POST, 시험 결과 열람은 `POST .../exam-result-open/`에서 정확한 대상 접근을 다시
검증한 뒤 ID와 제목을 `student_activity.target_open`에 남긴다. 해당 GET은 모두
읽기 전용이다. 연결된 학부모의 열람은 허용하되 학생 본인의 활동으로 기록하지
않는다. 종료 강의의 영상 재생 횟수는 이 학생별
target 기록만 집계하며 전체 영상 조회수나 다른 학생 기록을 섞지 않는다. 이 세부
기록 역시 기능 배포 이후부터 쌓이며 과거 횟수를 추정하지 않는다.

일반 학생 사용은 `actor_mode=student`, 대리보기 사용은
`actor_mode=support`로 저장한다. 후자는 실제 교직원을 `actor_user`로 남기므로
학생 행동으로 오인할 수 없다. 이벤트는 `target_tenant`와 `target_user`를 모두
가진 `OpsAuditLog`에 저장하며 `(target_user, -created_at)` 인덱스로 조회한다.

## 교직원 조회 API

`GET /api/v1/students/<student_id>/activities/`는 요청 테넌트의 직원에게만
허용한다.

- 기간은 7일·30일·90일 중 하나이며 기본값은 30일이다.
- 결과는 최신순 최대 100건이다.
- 분류 필터와 80자 이하 검색어 `q`를 지원한다. 검색은 활동 요약, 검증된 대상
  제목과 교직원 계정 스냅샷에 서버 측으로 적용되므로 최신 100건 바깥의 기록도
  먼저 검색 범위에 포함한다.
- 기본 응답은 `actor_mode=support`를 제외한다. `include_support=true`를 명시한
  경우에만 교직원 대리보기 기록도 함께 반환한다.
- `count`는 이번 응답 건수, `total_count`는 조건 전체 건수이며 `has_more`는 최신
  100건 제한 여부를 뜻한다. 각 결과는 사람이 읽을 수 있는 `actor_label`,
  검증된 `target_label`, 고객지원 확인용 `evidence_id`를 제공한다.
- GET은 읽기 전용이다. backend가 먼저
  `POST /api/v1/students/<student_id>/activities/view/`를 배포한 뒤 프런트가 조회
  직전에 같은 필터를 POST한다. 서버는 학생과 교직원 권한을 다시 확인한 뒤
  `student_activity.view` 감사 로그를 남긴다. legacy GET 감사 fallback은 없으며,
  감사 POST가 실패하면 활동 내역을 열지 않는다.

## 실패 동작과 검증

- 학생 없음: 404
- 비활성 학생: 대리보기 409
- 잘못된 기간·분류·기기 값: 400
- 학생이 아닌 계정 또는 허용되지 않은 화면 ID의 기록: 403
- 다른 테넌트 학생: 존재 여부를 넓히지 않고 404
- 종료되었거나 DB 수명주기 행이 없는 대리보기 토큰: 401

집중 회귀는 다음을 증명한다.

```powershell
python manage.py test apps.domains.students.tests.test_student_support -v 2
```

테스트는 실제 로그인 1건, access-only 15분 세션, 대리보기 로그인 미기록,
student/support actor 분리, 운영자 권한 회수 즉시 차단, 기본 제외·명시 포함,
분류·검색 필터, 전체 건수·증거 상세, 팝업/교직원 종료 뒤 즉시 토큰 차단과
테넌트 격리를 검증한다.
