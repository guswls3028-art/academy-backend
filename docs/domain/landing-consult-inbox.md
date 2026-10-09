# 홈페이지 상담 수신함

외부 방문자가 홈페이지 상담 폼으로 보낸 문의를 현재 학원의 owner/admin이
읽고 처리 메모를 남긴다. 공개 접수 API와 개인정보 동의 계약은 유지하며,
수신함 API는 인증·현재 학원 회원 역할과 tenant 조건을 모두 통과해야 한다.
다른 학원 문의는 읽거나 수정할 수 없고 teacher/staff의 전역 직원 표시는
관리 권한을 대신하지 않는다. 기존 문의·연락처·메모·동의 증빙은 변경/이관하지 않는다.

## 조회와 집계

`GET /api/v1/core/landing/admin/consult/`은 기존 `items`, `summary`를 유지한다.
`summary.total/unread`는 현재 학원의 전체 문의를 집계한다. 최근 200개로 자른
목록을 집계하던 방식은 과거 미확인 문의를 숨겼으므로 교체했다.

- `page`: 1 이상 1,000,000 이하. 기본 1. 삭제/읽음 처리로 마지막 페이지가 줄면
  현재 마지막 페이지로 보정한다. 없는 페이지를 빈 성공 목록으로 오인하지 않는다.
- `page_size`: 1–200, 기본 200으로 기존 응답 크기를 보존한다. 화면은 50개씩 읽는다.
- `filter`: `all`(기본) 또는 `unread`. 필터의 개수는 `pagination.count`이며
  `summary`는 필터와 관계없이 전체 집계다.
- `summary_only=true`: 알림 건수 조회에는 연락처/메모 행을 보내지 않고 빈
  `items`와 전체 `summary`만 반환한다.
- 일반 목록의 `pagination`은 `page/page_size/pages/count/has_next/has_previous`를
  반환한다. 정렬은 `-created_at,-id`로 같은 생성 시각의 순서를 고정한다.
  이는 변경 불가능한 데이터 스냅샷을 보장하는 계약은 아니다.

잘못된 범위·필터는 400이다. 알 수 없는 tenant/권한은 기존 거부 정책을 따른다.
서버를 먼저 배포하므로 기존 화면은 `items/summary`로 계속 동작하고, 새 화면은
페이지와 필터를 URL에 보존한다. 조회 실패는 내용과 합계를 성공처럼 표시하지 않고
같은 페이지를 재조회한다.

## 읽음과 메모 저장

`PATCH .../consult/{id}/`는 JSON 객체의 `mark_read`와 `admin_memo`를 검증한다.
메모는 최대 2,000자이며 공백과 줄바꿈을 보존한다. 빈 문자열/null은 기존처럼
메모 지우기다. 초과/잘못된 자료형은 기존 메모를 자르거나 덮어쓰지 않고 거절한다.
`mark_read=false`는 읽음을 취소하지 않는다.

SQL은 요청이 명시한 필드만 수정하고 읽음은 `COALESCE(read_at, now)`로 기록한다.
따라서 메모 저장과 읽음 처리의 순서가 겹쳐도 한 요청의 과거 스냅샷이 다른 필드를
되돌리지 않으며, 반복 읽음은 최초 읽음 시각을 유지한다. 서로 다른 사용자가
같은 메모를 동시에 편집한 경우의 버전 충돌 감지는 이 계약에 추가하지 않는다.

검증 소유: `apps/core/tests/test_landing_consult_inbox.py`의 실제 JWT·205개 동시각
문의·과거 미확인·전체 집계·페이지 보정·입력 보존·SQL 사이에 끼어든 읽음 처리와
기존 `test_landing_consult_privacy_consent.py`. 화면과 PC/390px 검증은
[학원 설정 계약](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/ORGANIZATION-SETTINGS.md)이 소유한다.
로컬/CI 증거와 동일 산출물 격리 실사용·운영 확인은 따로 기록한다.
