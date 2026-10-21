# 메시징 도메인 SSOT 인덱스

**상태:** Active
**최종 점검:** 2026-09-28
**목적:** 오래된 메시징 표가 여러 문서에 평행 진실로 남는 것을 막기 위한 현재 SSOT 진입점.

## 1. 권위 순서

| 영역 | 정본 |
|------|------|
| 트리거 정책 분류 | `apps/domains/messaging/policy.py`의 `TRIGGER_POLICY` |
| 자동 발화 구현 여부 | `apps/domains/messaging/policy.py`의 `IMPLEMENTED_AUTO_TRIGGERS` |
| 기본 템플릿 정의 | `apps/domains/messaging/default_templates.py` |
| 알림톡 템플릿/봉투 정책 | [messaging-alimtalk.md](messaging-alimtalk.md) |
| 운영 정책 표 | `backend/docs/ssot/messaging-policy.md` |
| 계정 복구 알림톡 | [account-recovery.md](account-recovery.md) |
| 수동 알림 컨텍스트 소스 | `apps/support/messaging/manual_context_sources.py` |

낡은 이벤트 표, Solapi ID 표, 구현 예정 목록을 이 파일에 다시 복제하지 않는다. 위 정본 중 하나를 갱신하고 이 인덱스에는 경로만 남긴다.

## 2. 현재 핵심 정책

- 신규 카카오 알림톡 템플릿 검수/등록을 기본 제안하지 않는다. 기존 4종 ITEM_LIST 봉투 + `#{선생님메모}` 자유 본문 정책을 우선 적용한다.
- 모든 실발송은 공용 Solapi 계정의 알림톡만 사용한다. 기본은 공용 owner 채널이고, 운영자가 새 `AlimtalkChannelBinding`으로 검증한 tenant는 승인·본문지문 일치 템플릿만 자기 채널로 치환한다. SMS/LMS, 과거 tenant PFID/provider/자체 키는 사용하지 않는다.
- SMS/LMS 예외는 없다. `check_dev_alerts`는 운영 룰을 평가해 설정된 Slack webhook으로만 알리며, SMS 설정·테스트·외부 신호 발송 옵션은 존재하지 않는다. 운영 절차는 `docs/operations/runbooks/incidents.md`가 정본이다.
- 신규 발송 경계는 `enqueue_alimtalk()` 하나다. 명시된 비알림톡 `message_mode`는 알림톡으로 보정하지 않고 차단하며, 기존 로그와 테넌트별 공급자/발신번호/키 값은 삭제하지 않고 이력 데이터로 보존한다.
- 계정 관련 시스템 알림(가입 승인, 아이디 찾기, 비밀번호 찾기)은 `send_alimtalk_via_owner()`를 통해 오너 테넌트 exact trigger 승인 템플릿으로 발송한다.
- 알림톡 템플릿 fallback은 금지한다. exact 공용 승인 템플릿 또는 명시 unified category가 없으면 발송하지 않는다.
- 공용 트리거 운영 실발송 검증은 `scripts/v1/run-messaging-verify-send.ps1` → `messaging_verify_common_alimtalk`을 사용한다. 수동 UI 경로 검증은 프론트의 `e2e/stability/controlled-real-alimtalk-send.spec.ts`를 사용한다. 둘 다 수신번호를 `01031217466` 하나로 강제하며, 한 검증에서는 한 경로만 1회 실행하고 `NotificationLog.provider_message_id`와 공급사 최종 성공을 확인한다.
- `password_find_otp`는 legacy OTP 경로용 트리거다. 공개 로그인 화면의 현재 정본은 `/api/v1/auth/account-recovery/dispatch/`다.
- 수동/자동 발송 UX와 템플릿 본문 자유 정책은 [messaging-alimtalk.md](messaging-alimtalk.md)와 `backend/docs/ssot/messaging-policy.md`를 우선한다.
- 클리닉 변경 알림처럼 도메인 상태에서 파생되는 수동 발송 변수/대상자는 프론트에서 재구현하지 않고 `context_source`로 백엔드 정본에 위임한다.
- `context_source`가 만든 변수 키는 서버 계산값이 정본이다. 요청 `context`/`context_per_student`가 같은 키를 보내면 미리보기 API에서 거부한다.
- 수동 발송의 최종 카카오 미리보기는 preflight의 `preview_recipients[].full_message_body`가 정본이다. 이 값은 실제 Solapi replacements와 같은 서버 계산값으로 만들며, 클라이언트 샘플 문구로 대체하지 않는다.

### 기본 문구의 삭제와 복원

대표·관리자는 `GET /api/v1/messaging/provision-defaults/`에서 복원 가능한 기본 문구 키·이름·분류를 읽고, `POST`의 `restore_keys`에 선택한 키만 넣어 복원한다. 일반 `POST`는 새 테넌트의 기본 문구와 아직 설정되지 않은 트리거를 준비하지만 기존 자동발송의 선택 문구, 빈 연결, 삭제한 기본 문구를 덮어쓰거나 다시 연결하지 않는다. 기존 테넌트에서 과거에 제거된 자유양식 기본 문구의 부재도 유지한다. 응답의 기존 생성·연결 수는 유지하고 `suppressed_defaults`가 선택 복원 대상을 알려준다. 유효하지 않거나 중복된 키는 전체 요청을 거절한다. 이 API는 공급사 검수나 발송을 수행하지 않는다.

문구 삭제는 같은 테넌트에 한정된다. 자동발송 설정이 참조하는 문구는 다른 문구로 변경하거나 연결을 해제하기 전까지 `409 auto_send_linked`로 거절한다. 공급사 템플릿 식별자·검수 상태가 있는 문구는 `409 provider_bound`로 보존한다. 그 밖의 시스템 제공 문구는 대표·관리자가 삭제할 수 있고, 교사는 복제한 사용자 문구를 편집·삭제한다. 삭제 가능한 기본 문구는 `DefaultTemplateSuppression(tenant, default_key)`에 선택을 기록한 뒤 삭제하므로 다음 일반 프로비저닝에서 되살아나지 않는다. 목록/상세의 읽기 전용 `can_delete`·`delete_block_reason`은 이 조건을 표시하며 삭제 API가 최종 확인한다. 기존 문구 본문과 제목, 자동발송의 enabled·시점·참조, 과거 발송 기록은 마이그레이션에서 변경하지 않는다. 이전 버전에서 삭제된 문구는 삭제 이력을 역산하지 않지만, 기존 빈 연결과 빠진 자유양식 기본 문구는 일반 프로비저닝에서 부재로 취급한다. 연결 해제 뒤 명시 복원 또는 다른 문구 선택으로 복구한다.

검증은 삭제→재조회→일반 프로비저닝 후 부재, 선택 키 하나만 복원, 기존 선택 문구·타 테넌트 불변, 참조 중/공급사 문구 삭제 거절, 사용자 문구 복제·수정과 실제 발송의 승인 봉투 경계를 각각 확인한다. UI 흐름은 프런트엔드 메시징 소유 문서를 따른다.

### 새 문구 생성의 응답 유실 복구

`POST /messaging/templates/`는 선택적 UUID 문자열 `client_request_id`를 받는다.
한 번의 저장 의도에는 같은 key와 같은 본문·제목·이름·분류·기본 지정 payload를
재사용한다. 학원 행 잠금 안에서 최초 정규화 payload의 SHA-256과 생성 문구를
`MessageTemplateCreateRequest`에 함께 기록한다. key는 학원 범위이며 같은 학원의
허용된 직원에게만 기존과 같은 문구 읽기/생성 권한으로 적용한다. 원문·인증정보는
receipt에 저장하지 않는다. 첫 생성은 201, 같은 key/최초 payload 재시도는 같은
문구 ID의 **현재 저장 상태**를 200으로 반환한다. 그 사이 다른 직원이 수정한
본문·분류·기본 지정은 원래 생성 payload로 되돌리지 않으며 수정 시각도 바꾸지 않는다.
POST 응답의 `creation_content_changed`는 최초 생성 시 false이며, replay에서는 최초
정규화된 name/subject/body/category와 현재 문구가 다를 때만 true다. 앞뒤 공백 제거·분류
alias 정상화와 기본 지정 flag만의 변화는 내용 충돌이 아니다. 편집기는 이 서버 판정을
사용해 true일 때 원래 초안과 최신 문구를 비교하고 자동 기본 지정을 보류한다.

같은 key의 다른 payload는 `409 template_request_conflict`이며, 문구가 명시적으로
삭제됐다면 receipt의 연결만 null로 남겨 `409 template_create_deleted`로 응답한다.
삭제된 문구를 자동 재생성하지 않는다. 편집기는 입력을 보존하고 사용자에게 새 저장
의도를 명시적으로 시작하게 해야 한다. 새 이름/본문만으로 기존 문구를 자동 중복
제거하지 않으며 다른 key는 별도 생성이다. key 없는 기존 POST·복제는 그대로다.
잘못된/null UUID는 400이고 PATCH에서 생성 key를 바꾸는 요청도 400이다.

0044 expand migration은 receipt 표와 tenant/key 유일 제약만 추가한다. 기존 문구를
변경하거나 과거 요청을 추정하지 않으며 이전 앱은 새 표 없이 기존 계약으로 동작한다.
문구 삭제는 receipt tombstone을 남기고 학원 삭제는 receipt도 제거한다. rolling 중
receipt 모델을 모르는 구버전 collector/SQL 삭제도 지원하도록 PostgreSQL의 새 표 FK는
DB 수준 `ON DELETE SET NULL`/`CASCADE`를 사용한다. SQLite 검증 DB는 기존 FK를
유지하며 동일 동작의 삭제 trigger를 설치한다. 현재 ORM collector에도 같은 삭제
정책을 유지한다. migration 역방향은 trigger를 제거한 뒤 새 표만 제거한다. 새 코드를
쓰기 전 migration을 적용하고 코드 rollback 시 새 표는 보존한다. 이 migration을
되돌리면 새 요청 재시도 증거가 사라지므로 제품 rollback의 필수 단계가 아니다.
회귀는 commit 후 동일 재시도·이후 편집/기본 지정 보존·다른 payload 409·삭제 뒤
재시도·tenant/role 격리·원자적 rollback·legacy 생성을 검증하며 PostgreSQL에서는
동일 key의 동시 생성이 단일 문구와 receipt로 수렴하는지 확인한다. 외부 발송은 없다.

### 공유 문구의 동시 편집과 재시도

학원 직원이 기존 사용자 문구를 `PATCH /messaging/templates/{id}/`로 수정할 때,
읽은 응답의 `updated_at`을 선택 필드 `expected_updated_at`으로 보낼 수 있다.
최초 조회 뒤 다른 직원이 문구를 삭제한 경우 잠금 후 재조회도 `404`로 응답하며, 삭제된 문구를 재생성하지 않는다.
서버는 기존 학원 행 잠금 안에서 현재 문구를 다시 읽고 비교한다. 다른 직원의
수정이 먼저 저장됐으면 `409 template_conflict`와 현재 tenant/권한 범위의
`current_template`을 반환하며 본문·분류·기본 지정은 변경하지 않는다. 편집기는
작성 초안을 보존하고 최신본을 적용하거나, 비교 후 최신 `updated_at`을 조건으로
초안을 명시적으로 다시 저장한다. 시각은 ISO 8601이며 null/잘못된 값은 400이다.

요청한 수정 필드가 이미 현재 값과 모두 같으면, 성공 응답이 유실된 동일 요청의
재시도는 200으로 응답하고 `updated_at`도 다시 바꾸지 않는다. 이는 새 문구 POST의
중복 생성 방지 계약은 아니다. 조건을 생략한 기존 PATCH는 계속 지원하지만
동시 편집 충돌 보호는 제공하지 않는다. 생성·복제, 시스템 문구 읽기 전용,
직원 권한·다른 학원 비공개 및 기존 데이터는 유지하며 migration은 없다.
실제 URL/API 회귀는 stale 409·최신본 확인 후 재적용·동일 재시도·분류별 기본
보존·잘못된 시각·다른 학원/학생/학부모/미인증 거절을 검증한다. 외부 발송은 없다.

### 발송 기본 문구 선택

사용자 기본 문구는 학원·분류마다 하나다. 문구 생성/PATCH에서 기본을 지정하거나
기본 문구를 다른 분류로 이동하면, 같은 트랜잭션에서 해당 분류의 이전 기본을
해제한 뒤 저장한다. 기본 지정/해제 전용 API도 같은 학원 행 잠금을 사용하므로
동시 요청의 유일 제약 오류나 중간 해제 상태를 노출하지 않는다. 저장 실패는
이전 기본 선택까지 롤백한다. 다른 학원·분류, 문구 본문, 자동발송 연결은 보존한다.

`POST /api/v1/messaging/templates/{id}/set-default/`는 선택적 JSON boolean
`is_user_default`로 원하는 상태를 명시할 수 있다. 같은 값을 재요청해도 상태를
뒤집지 않으므로 서버 commit 뒤 응답만 유실된 경우 안전하게 재시도할 수 있다.
필드 누락은 기존 toggle 호환을 유지하며 null·문자열·숫자는 400으로 거절한다.
시스템/사용자 양식의 기존 선택 권한과 학원·분류별 유일성, 다른 학원 격리는 유지한다.
본문 편집·공급사 검수·발송은 이 요청으로 수행하지 않는다.

## 3. 변경 규칙

메시징 코드를 바꾸면 다음을 함께 확인한다.

1. `policy.py`의 정책 분류와 구현 여부가 실제 호출 경로와 맞는가.
2. `default_templates.py`의 변수명이 Solapi 승인 변수와 맞는가.
3. [messaging-alimtalk.md](messaging-alimtalk.md)의 봉투/편지 정책과 충돌하지 않는가.
4. [account-recovery.md](account-recovery.md)의 계정복구 발송 흐름과 충돌하지 않는가.
5. 수동 발송 컨텍스트가 도메인 상태에서 파생된다면 `manual_context_sources.py` 또는 해당 도메인 서비스가 정본인가.
6. `context_source` 기반 변수 키가 클라이언트 입력으로 덮이지 않는가.
7. 최종 미리보기가 실제 Solapi replacements 기반 서버 문구를 사용하고, 계약 누락 시 fail-close하는가.
8. 오래된 표나 legacy 안내를 추가하지 않았는가.

## 4. 정리 이력

- 2026-05-21: 2026-04-08 기준의 장문 이벤트 표를 제거하고 SSOT 인덱스로 전환. 최신 정책은 `policy.py`, `messaging-alimtalk.md`, `messaging-policy.md`, `account-recovery.md`로 분리.
- 2026-06-06: 공용 오너 알림톡 only 및 fallback 금지 정책을 현재 SSOT에 반영. provider id 로그와 통제번호 전용 운영 검증 명령 추가.
- 2026-07-26: 수동 발송의 서버 정본 `full_message_body`, 학생별 최종 카카오 미리보기, 통제번호 UI 실발송 경로를 반영.
- 2026-08-20: 운영자 SMS 예외를 폐기하고 제품·운영의 모든 휴대전화 실발송을 공용 카카오 알림톡으로만 고정.
- 2026-08-21: SMS 호환 callable·capability 필드·이름이 남은 throttle을 제거하고 비알림톡 입력을 API부터 worker까지 실패 폐쇄하도록 정리.

### 실제 수업 정보 블록과 성적 안내문 저장

성적 화면의 수동 preflight/send 요청은 `session_id`로 기준 수업을 전달한다.
`#{현재차시명}`, `#{현재차시날짜}`, `#{다음차시명}`, `#{다음차시날짜}`와
`#{N차시후명}`/`#{N차시후날짜}`(1~99)는 기준 수업과 같은 학원·강의·반에서
`order, id` 순으로 실제 차시를 선택한다. 제목 숫자를 증가시키거나 날짜를
계산하지 않는다. 날짜는 Session.date이며 `#{날짜}`/`#{시간}`은 별도의 발송
예정 시각(즉시 발송은 현재 시각)이다. `#{강의날짜}`는 실제 수업 날짜를 우선한다.
서버가 공유/학생별 컨텍스트와 `_body_subst`를 같은 값으로 조립하며, 요청의
임의 수업 변수는 서버 값으로 덮는다. 기준 수업·반·수신 학생의 수강/차시
등록을 학원 범위에서 확인한다. 수신 대상은 성적표와 같은 출결 우선 차시
명단을 사용하므로 차시 수강 행 없이 출결로 편입된 활성 학생도 정상 발송할
수 있다. 비활성 수강·명단에서 제외된 학생은 거절한다. 미래 차시나 날짜 누락은 preflight의
`session_context_missing` blocker와 send의 409로 중단하고, 차시 등록/날짜
수정 또는 해당 블록 제거 후 재검사하도록 안내한다. 실제 발송 없이 helper,
preflight, send 진입 차단 회귀 테스트로 검증한다.

성적 안내문은 본문 저장과 ‘성적 안내 기본으로 지정’을 분리한다. 새 문구 저장
시 기본 지정 여부를 명시적으로 선택하고, 기존 문구의 변경을 기본 지정할 때는
먼저 본문을 저장한다. 기본은 개인 설정이 아닌 해당 학원 직원 공통·분류별 하나이며
다음 모달/새로고침에서 저장된 기본을 불러온다. 본문은 승인된 시스템 봉투 안의
편지만 변경한다. 사용자 문구 자체의 공급사 검수 상태와 별개로 승인 봉투의
유효성을 기존 서버 preflight/send가 확인하며, 승인·발신 정책을 우회하지 않는다.
성적 블록 `#{과제총점}`/`#{과제총만점}`은 선택한 과제 합계 값이다.
