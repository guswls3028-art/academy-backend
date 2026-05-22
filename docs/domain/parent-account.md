# 학부모 계정 SSOT

**상태:** Active
**최종 점검:** 2026-05-21
**코드 기준:** `apps/domains/parents/services/__init__.py`, `apps/domains/parents/models.py`, `apps/api/common/auth_jwt.py`

## 1. 계정 생성 규칙

| 항목 | 현재 규칙 |
|------|-----------|
| 내부 username | `p_{tenant_id}_{parent_phone}` |
| 로그인 입력값 | 학부모 전화번호 |
| 초기 비밀번호 | 학부모 전화번호 숫자 기준 마지막 4자리 |
| 강제 변경 | `must_change_password=True` |
| 이름 | 기존 Parent 이름 우선, 없으면 `{학생이름} 학부모` |
| 역할 | `TenantMembership.role = "parent"` |
| 생성 시점 | 학생 등록 또는 계정복구 중 `ensure_parent_for_student()` 호출 |

`PARENT_DEFAULT_PASSWORD = "0000"` 상수는 외부 import 호환용 deprecated 값이다. 신규 코드에서 초기 비밀번호로 사용하지 않는다.

## 2. 생성/연결 플로우

```
학생 등록 또는 legacy 학부모 계정 복구
  -> ensure_parent_for_student(tenant, parent_phone, student_name)
    -> Parent(tenant + phone) 조회
    -> Parent 없음:
         User(username=p_{tenant_id}_{phone}, phone=phone, tenant=tenant) 생성
         password=parent_initial_password(phone)
         must_change_password=True
         Parent 생성
         TenantMembership(parent) 활성화
    -> Parent 있음 + user 없음:
         기존 Parent.name 보존
         User 생성/연결
         TenantMembership(parent) 활성화
    -> Parent 있음 + user 있음:
         기존 Parent 반환
```

## 3. 로그인

```
POST /api/v1/token/
Headers: X-Tenant-Code: {tenant_code}
Body: { "username": "{학부모전화번호}", "password": "{비밀번호}" }
```

테넌트 바인딩은 JWT 발급 과정에서 검증된다. 내부 username(`p_{tenant_id}_{phone}`)은 공개 로그인 입력값이 아니다.

## 4. 계정 복구와의 관계

공개 로그인 화면의 아이디/비밀번호 찾기는 [account-recovery.md](account-recovery.md)가 정본이다.

- 학부모 아이디 찾기: 학생 이름 + 등록 학부모 전화번호가 유일하게 일치할 때 전화번호로 아이디 안내를 보낸다.
- 학부모 비밀번호 찾기: 동일 검증 후 8자리 숫자 임시 비밀번호를 pending reset으로 발급한다. 실제 비밀번호 변경과 `must_change_password=True` 적용은 학부모가 임시 비밀번호로 로그인할 때 수행한다.
- legacy Parent row에 user가 없으면, 복구 과정에서 `ensure_parent_for_student()`로 계정을 생성/연결한다.

## 5. 가입 승인 알림톡

학부모 가입 안내는 `registration_approved_parent` 트리거를 사용한다.

| 변수 | 값 |
|------|----|
| `#{학부모아이디}` | 학부모 전화번호 |
| `#{학부모비밀번호}` | 최초 발급 시 전화번호 뒤 4자리, 아이디 찾기 시 `변경되지 않음` |
| `#{학생아이디}` | 학생 `ps_number` |
| `#{학생비밀번호}` | 가입 승인/학생 안내 값 또는 `변경되지 않음` |
| `#{비밀번호안내}` | 상황별 안내 문구 |

계정/비밀번호 복구 발송 정책은 `send_alimtalk_via_owner()`를 따른다. SMS fallback은 없다.

## 6. 유지보수 명령

`apps/domains/parents/management/commands/reset_all_parent_passwords.py`는 legacy 일괄 정비용 명령이다.

```
python manage.py reset_all_parent_passwords --dry-run
python manage.py reset_all_parent_passwords
```

전체 학부모 계정에 영향을 줄 수 있으므로 운영 실행 전 대상 row 수를 확인하고 사용자 명시 승인을 받아야 한다. 신규 초기 비밀번호 정책은 이 명령이 아니라 `parent_initial_password()`가 SSOT다.
