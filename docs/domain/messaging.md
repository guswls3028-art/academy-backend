# 메시징 도메인 SSOT 인덱스

**상태:** Active
**최종 점검:** 2026-07-26
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
- 모든 실발송은 공용 오너 알림톡만 사용한다. SMS/LMS, tenant별 PFID, tenant별 알림톡 provider는 신규 발송 경로에서 사용하지 않는다.
- 예외는 제품 메시징과 분리된 플랫폼 운영자 장애 SMS뿐이다. `check_dev_alerts`가 플랫폼 발급 테넌트 코드·내부 ID, 통제된 장애 분류와 건수 또는 화이트리스트 CloudWatch 장애 신호만 `01031217466`으로 보낸다. 다른 수신자·owner 수정 테넌트명·사용자 본문/경로·개인정보·제품 UI 호출은 코드에서 차단한다. 운영 절차, 90-byte 초과 시 `+N곳` 집계, 공급사 미확정 at-most-once 보류와 발송 상한 규칙은 `docs/operations/runbooks/incidents.md`가 정본이다.
- 계정 관련 시스템 알림(가입 승인, 아이디 찾기, 비밀번호 찾기)은 `send_alimtalk_via_owner()`를 통해 오너 테넌트 exact trigger 승인 템플릿으로 발송한다.
- 알림톡 템플릿 fallback은 금지한다. exact 공용 승인 템플릿 또는 명시 unified category가 없으면 발송하지 않는다.
- 공용 트리거 운영 실발송 검증은 `scripts/v1/run-messaging-verify-send.ps1` → `messaging_verify_common_alimtalk`을 사용한다. 수동 UI 경로 검증은 프론트의 `e2e/stability/controlled-real-alimtalk-send.spec.ts`를 사용한다. 둘 다 수신번호를 `01031217466` 하나로 강제하며, 한 검증에서는 한 경로만 1회 실행하고 `NotificationLog.provider_message_id`와 공급사 최종 성공을 확인한다.
- `password_find_otp`는 legacy OTP 경로용 트리거다. 공개 로그인 화면의 현재 정본은 `/api/v1/auth/account-recovery/dispatch/`다.
- 수동/자동 발송 UX와 템플릿 본문 자유 정책은 [messaging-alimtalk.md](messaging-alimtalk.md)와 `backend/docs/ssot/messaging-policy.md`를 우선한다.
- 클리닉 변경 알림처럼 도메인 상태에서 파생되는 수동 발송 변수/대상자는 프론트에서 재구현하지 않고 `context_source`로 백엔드 정본에 위임한다.
- `context_source`가 만든 변수 키는 서버 계산값이 정본이다. 요청 `context`/`context_per_student`가 같은 키를 보내면 미리보기 API에서 거부한다.
- 수동 발송의 최종 카카오 미리보기는 preflight의 `preview_recipients[].full_message_body`가 정본이다. 이 값은 실제 Solapi replacements와 같은 서버 계산값으로 만들며, 클라이언트 샘플 문구로 대체하지 않는다.

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
