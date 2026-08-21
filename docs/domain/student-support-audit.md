# 학생 지원 대리보기·활동 감사 계약

**상태:** Active  
**최종 확인:** 2026-08-22 KST  
**정본 구현:** `apps/domains/students/views/support_views.py`,
`apps/domains/students/services/activity.py`, `apps/api/common/auth_jwt.py`

## 목적과 권한

관리자·직원이 학생 문의를 처리할 때 학생 비밀번호로 다시 로그인하지 않고 실제
학생 앱을 볼 수 있어야 한다. `POST /api/v1/students/<student_id>/support-session/`
은 요청 테넌트의 활성 학생과 `TenantResolvedAndStaff` 권한이 모두 확인된 경우에만
대리보기 세션을 만든다. 다른 테넌트 학생을 추정하거나 찾는 fallback은 없다.

대리보기 토큰은 다음 경계를 가진다.

- 15분짜리 access token만 발급하며 refresh token은 만들지 않는다.
- `support_preview`, `impersonated_by`, `support_session_id`,
  `support_student_id` claim으로 일반 학생 세션과 구분한다.
- 시작은 `OpsAuditLog(action="student_support_view.start")`에 교직원 actor와 학생
  target을 함께 남긴다.
- 토큰 만료·계정 비활성화·테넌트 불일치 시 학생 API 권한이 닫힌다.

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

일반 학생 사용은 `actor_mode=student`, 대리보기 사용은
`actor_mode=support`로 저장한다. 후자는 실제 교직원을 `actor_user`로 남기므로
학생 행동으로 오인할 수 없다. 이벤트는 `target_tenant`와 `target_user`를 모두
가진 `OpsAuditLog`에 저장하며 `(target_user, -created_at)` 인덱스로 조회한다.

## 교직원 조회 API

`GET /api/v1/students/<student_id>/activities/`는 요청 테넌트의 직원에게만
허용한다.

- 기간은 7일·30일·90일 중 하나이며 기본값은 30일이다.
- 결과는 최신순 최대 100건이다.
- 분류 필터를 지원한다.
- 기본 응답은 `actor_mode=support`를 제외한다. `include_support=true`를 명시한
  경우에만 교직원 대리보기 기록도 함께 반환한다.
- 조회 자체는 `student_activity.view` 감사 로그를 남긴다.

## 실패 동작과 검증

- 학생 없음: 404
- 비활성 학생: 대리보기 409
- 잘못된 기간·분류·기기 값: 400
- 학생이 아닌 계정 또는 허용되지 않은 화면 ID의 기록: 403
- 다른 테넌트 학생: 존재 여부를 넓히지 않고 404

집중 회귀는 다음을 증명한다.

```powershell
python manage.py test apps.domains.students.tests.test_student_support -v 2
```

테스트는 실제 로그인 1건, access-only 15분 세션, 대리보기 로그인 미기록,
student/support actor 분리, 기본 제외·명시 포함, 분류 필터와 테넌트 격리를
검증한다.
