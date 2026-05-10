# 학부모 계정 시스템 SSOT

> 최종 검증일: 2026-03-17 (E2E 테스트 통과)

## 1. 학부모 계정 생성 규칙

| 항목 | 값 |
|------|-----|
| 아이디(username) | 학부모 전화번호 (내부: `p_{tenant_id}_{phone}`) |
| 초기 비밀번호 | `0000` (상수: `PARENT_DEFAULT_PASSWORD`) |
| 이름 | `{학생이름} 학부모` (자동 생성) |
| 역할 | `TenantMembership.role = "parent"` |
| 생성 시점 | 학생 등록 시 `ensure_parent_for_student()` 자동 호출 |

## 2. 현재 비밀번호 설정 (2026-03-17 적용)

| 테넌트 | 비밀번호 | 대상 수 | 검증 |
|--------|---------|---------|------|
| Tenant 2 (tchul) | `1234` | 17명 | API E2E PASS |
| Tenant 1 (hakwonplus) | `0000` | 118명 | API + Browser E2E PASS |
| Tenant 3 (limglish) | `0000` | 46명 | API E2E PASS |
| Tenant 8 (sswe) | `0000` | 71명 | API E2E PASS |
| Tenant 9999 | `0000` | 8명 | API E2E PASS |

**변경 범위**: TenantMembership `role='parent'`인 유저만. owner/admin/teacher/staff/student 계정은 일절 미변경 (검증 완료).

## 3. 학부모 계정 자동 생성 플로우

```
POST /api/v1/students/ (학생 등록)
  → StudentViewSet.create()
    → ensure_parent_for_student(tenant, parent_phone, student_name)
      → Parent 조회 (tenant + phone)
      → 없으면: User 생성 (username=p_{tid}_{phone}, password=0000)
               Parent 생성
               TenantMembership 생성 (role=parent)
      → 있으면: 기존 Parent 반환 (User 없으면 생성)
    → send_welcome_messages() (send_welcome_message=true 일 때)
      → 카카오톡(알림톡) 발송
```

### E2E 검증 결과 (2026-03-17)

1. **학생 등록**: ✅ `POST /api/v1/students/` → 201 Created
2. **학부모 계정 자동 생성**: ✅ 아이디=전화번호, 비번=0000
3. **학부모 로그인**: ✅ JWT 발급 성공
4. **카카오톡(알림톡) 발송**: ✅ success=True, mode=alimtalk

## 4. 카카오톡(알림톡) 발송

### 학부모 가입 안내 템플릿 (`registration_approved_parent`)

```
{학생이름}학생 학부모님, 안녕하세요.
가입 신청이 승인되었습니다.

▶ 학부모 로그인 정보
아이디: {학부모전화번호}
비밀번호: 0000

▶ 학생 로그인 정보
아이디: {학생PS번호}
비밀번호: {학생비밀번호}

▶ 접속 링크
{사이트링크}
```

- 발송 채널: Solapi → KakaoTalk 알림톡
- 발송 조건: `send_welcome_message=true` (학생 등록 시 선택)
- 발송 로그: `NotificationLog` 테이블

## 5. 학부모 로그인 방법

```
POST /api/v1/token/
Headers: X-Tenant-Code: {tenant_code}
Body: { "username": "{전화번호}", "password": "{비밀번호}" }
→ 200: { "access": "...", "refresh": "..." }
```

- 로그인 시 학생 ID로 먼저 조회 → 실패 시 학부모 전화번호로 fallback 조회
- 테넌트 해석: `X-Tenant-Code` 헤더 또는 요청 body의 `tenant_code`

## 6. 관련 코드

| 파일 | 역할 |
|------|------|
| `apps/domains/parents/services.py` | `ensure_parent_for_student()`, `PARENT_DEFAULT_PASSWORD` |
| `apps/domains/parents/models.py` | Parent 모델 |
| `apps/domains/students/views.py` | StudentViewSet.create() |
| `apps/support/messaging/services.py` | `send_welcome_messages()` |
| `apps/support/messaging/default_templates.py` | 알림톡 템플릿 |
| `apps/api/common/auth_jwt.py` | TenantAwareTokenObtainPairSerializer |

## 7. 비밀번호 일괄 초기화 커맨드

```bash
# 전체 학부모 → 0000 초기화
python manage.py reset_all_parent_passwords --dry-run  # 대상 확인
python manage.py reset_all_parent_passwords            # 실행
```

파일: `apps/domains/parents/management/commands/reset_all_parent_passwords.py`

> ⚠️ 이 커맨드는 테넌트 구분 없이 전체 초기화. 테넌트별 차등 비밀번호 필요 시 Django shell 사용.
