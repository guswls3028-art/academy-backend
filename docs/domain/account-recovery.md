# 계정 복구 SSOT

**상태:** Active
**최종 점검:** 2026-05-23
**코드 기준:** `apps/core/views/account_recovery.py`, `apps/domains/students/services/account_recovery.py`, `apps/core/services/password.py`, `apps/api/common/auth_jwt.py`

## 1. 정본 경로

공개 로그인 화면의 아이디 찾기/비밀번호 찾기는 하나의 계정복구 경로를 사용한다.

```
POST /api/v1/auth/account-recovery/dispatch/
{
  "mode": "username" | "password",
  "target": "student" | "parent",
  "student_name": "...",
  "phone": "01012345678"
}
```

프론트 정본:

- `frontend/src/auth/pages/AccountRecoveryModal.tsx`
- `frontend/src/auth/api/recovery.api.ts`

백엔드 정본:

- HTTP entry: `apps/core/views/account_recovery.py`
- domain service: `apps/domains/students/services/account_recovery.py`
- password SSOT: `apps/core/services/password.py`

## 2. 보안/테넌트 원칙

- `AllowAny` 엔드포인트지만 `TenantResolved`가 필수다.
- `SmsEndpointThrottle`을 적용한다.
- 요청 전화번호는 숫자만 남긴 뒤 `010`으로 시작하는 11자리만 허용한다.
- 조회 실패, 동명이인/공유번호 등 다건 매칭, 성공 모두 공개 응답은 generic message로 통일한다.
- API 응답에 아이디나 비밀번호를 직접 반환하지 않는다.
- 안내 발송은 검증된 전화번호로만 수행한다.
- legacy 공개 호환 경로도 정본 계정복구 서비스로 위임하며, 사용자 존재 여부를 노출하지 않는다.

## 3. 매칭 규칙

### 학생 대상

학생 이름 + 학생 전화번호 또는 학부모 전화번호가 현재 테넌트의 active student와 유일하게 일치해야 한다.

```
Student(tenant, deleted_at is null, name__iexact)
  where phone == 요청번호 or parent_phone == 요청번호
  exactly one match
  student.user exists
```

아이디 안내에는 학생 `ps_number`를 우선 사용하고, 없으면 user display username을 사용한다.

### 학부모 대상

학생 이름 + 등록 학부모 전화번호가 현재 테넌트의 active student와 유일하게 일치해야 한다.

```
Student(tenant, deleted_at is null, name__iexact, parent_phone == 요청번호)
  exactly one match
  Parent(tenant, phone) user exists or ensure_parent_for_student()로 생성/연결
```

학부모 공개 아이디는 전화번호다.

## 4. mode별 동작

### `mode=username`

- 비밀번호를 변경하지 않는다.
- 학생은 `registration_approved_student`, 학부모는 `registration_approved_parent` 트리거를 재사용한다.
- 비밀번호 변수에는 `변경되지 않음`을 넣는다.
- `#{비밀번호안내}`로 비밀번호를 잊었으면 비밀번호 찾기를 사용하라고 안내한다.

### `mode=password`

- `generate_temp_password()`로 6자리 숫자 임시 비밀번호를 만든다.
- 공개 계정복구는 즉시 비밀번호를 바꾸지 않고 `PendingPasswordReset`에 임시 비밀번호 해시를 저장한다.
- 학생은 `password_reset_student`, 학부모는 `password_reset_parent` 트리거로 발송한다.
- enqueue 실패 시 이번 요청의 pending reset을 되돌린다. 기존 pending reset이 없으면 삭제하고, 이미 발급된 기존 pending reset이 있으면 복원한다.
- 사용자가 알림톡의 임시 비밀번호로 로그인하면 `TenantAwareTokenObtainPairSerializer`가 pending reset을 소비하고 그때 `force_reset_password()`를 적용한다.
- pending reset이 소비되면 실제 비밀번호가 임시 비밀번호로 바뀌고 `token_version`이 증가하며 `must_change_password=True`가 된다.
- 비활성/로그인 불가 계정은 pending reset을 소비하지 않는다.
- pending reset이 만료되거나 워커/공급자 단계에서 발송 실패하더라도 기존 비밀번호는 그대로 유지된다.

임시 비밀번호 길이/형식 SSOT:

```
apps/core/services/password.py
TEMP_PASSWORD_LENGTH = 6
generate_temp_password() -> 숫자 6자리
```

비밀번호 최소 길이 정책은 `.claude/rules/domain.md §8`에 따라 4자 유지다. 자동 임시 비밀번호가 6자리인 것은 알림톡을 보고 직접 입력하는 학부모/학생 사용성을 위한 운영 정책이며 최소 길이 상향이 아니다.

## 5. Pending reset 안전 구조

공개 계정복구의 상품 안전 목표는 "알림톡을 못 받은 사용자를 기존 계정에서도 잠그지 않는 것"이다.

| 단계 | 상태 |
|------|------|
| 요청 성공 + enqueue 성공 | 임시 비밀번호 해시가 `PendingPasswordReset`에 저장됨. 기존 비밀번호는 계속 유효 |
| 사용자가 임시 비밀번호로 로그인 | pending reset 소비 → 실제 비밀번호 변경 → `must_change_password=True` |
| enqueue 실패 | 이번 요청의 pending reset 롤백. 기존 pending reset이 있으면 복원. 기존 비밀번호 유지 |
| 워커/공급자 단계 실패 | pending reset은 남아 있을 수 있으나 기존 비밀번호는 유지. 운영 확인은 NotificationLog/공급자 로그로 판단 |
| 만료 후 임시 비밀번호 로그인 | pending reset 삭제 후 일반 로그인 실패 처리 |

보관 원칙:

- plaintext 임시 비밀번호는 DB에 저장하지 않는다. pending reset은 Django password hash만 저장한다.
- `NotificationLog.message_body`는 계정/인증 트리거 본문을 저장하지 않고 보안 placeholder로 마스킹한다.
- 사용자별 pending reset은 1개만 유지한다. 새 요청은 이전 pending reset을 대체한다.
- 관리자가 인증된 상태에서 수행하는 학생/학부모 비밀번호 변경은 기존처럼 즉시 reset 경로를 사용할 수 있다.

## 6. 알림톡 발송

계정복구 발송은 `send_alimtalk_via_owner()`를 사용한다.

- 오너 테넌트의 승인된 알림톡 템플릿으로 발송한다.
- SMS fallback은 없다.
- `password_reset_*`, `password_find_otp` 템플릿이 승인되지 않았으면 가입 승인 템플릿으로 fallback할 수 있다.
- 테스트 테넌트의 메시징 disabled 상태에서는 실제 발송을 건너뛰고 성공으로 간주한다. 비밀번호 복구는 전달되지 않는 임시 비밀번호를 만들지 않기 위해 pending reset도 만들지 않는다.

## 7. Legacy compatibility

아래 경로는 기존 호출처 호환을 위해 남아 있다. 신규 공개 복구 UI는 추가하지 않는다.

| 경로 | 현재 용도 |
|------|-----------|
| `/api/v1/students/password_find/request/` | legacy OTP 발급 |
| `/api/v1/students/password_find/verify/` | legacy OTP 검증 + 새 비밀번호 설정 |
| `/api/v1/students/password_reset_send/` | 관리자/선생님 비밀번호 재설정 + 공개 비밀번호 복구 호환 |
| `/api/v1/students/send_existing_credentials/` | legacy 중복가입 자격 안내 호환 |

legacy 공개 호환 규칙:

- 비인증/비staff `password_reset_send` 요청은 정본 비밀번호 복구 서비스로 위임하며 pending reset을 사용한다.
- `send_existing_credentials` 요청은 정본 비밀번호 복구 서비스로 위임하며 pending reset을 사용한다.
- 공개 호환 요청의 발송 대상은 요청자가 증명한 전화번호다. 저장된 다른 학생/학부모 번호로 대체 발송하지 않는다.
- 조회 실패, 다건 매칭, parent side-effect 차단 케이스는 generic 200으로 응답하고 비밀번호를 변경하지 않는다.
- `temp_password`, `skip_notify`는 인증된 관리자/선생님 요청에서만 호환 허용한다.

이 경로를 수정하는 작업은 정본 서비스(`apps/domains/students/services/account_recovery.py`, `apps/core/services/password.py`)로 위임하거나, 호출처가 사라진 뒤 제거한다. 새 기능은 legacy view에 직접 추가하지 않는다.

## 8. 상품/파괴 테스트 기준

- 단위/통합: `python -m pytest apps\domains\students\tests\test_account_recovery.py apps\domains\students\tests\test_password_reset_safety.py -v --tb=short -x`
- 로그인 활성화: pending 임시 비밀번호로 `/api/v1/token/` 로그인이 성공하고 `must_change_password=True` 토큰이 발급되는지 확인한다.
- 기존 비밀번호 보호: 발송 실패, unknown account, ambiguous match, 워커/공급자 실패 상황에서 기존 비밀번호가 유지되는지 확인한다.
- 개인정보 보호: unknown/ambiguous/success 공개 응답은 generic message로 구분되지 않아야 한다.
- 반복 요청: 새 pending reset이 이전 pending reset을 대체해야 한다.
- 실패 복원: 이미 발급된 pending reset이 있을 때 다음 발송이 실패하면 기존 pending reset이 유지되어야 한다.
- 실발송: 운영 설정에서 실제 알림톡 enqueue와 워커 발송 성공을 확인해야 한다.
- 단말 확인: 실사용 번호로 받은 알림톡 본문을 확인해야 상품 QA가 닫힌다.
- 테스트 데이터는 `[E2E-{timestamp}]` 태그를 사용하고 cleanup한다.
