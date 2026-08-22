# 신규 계정 첫 접속 안내

**상태:** Active
**코드 기준:** `apps/core/models/user.py`, `apps/core/serializers.py`, `apps/core/views/auth.py`

## 목적과 범위

배포 이후 새로 만들어진 테넌트 계정은 첫 인증 세션에서 로그인 아이디와
비밀번호·프로필 설정 위치를 짧게 안내받는다. 이 안내는 비밀번호 변경을
강제하지 않는 권유형 UI이며, 비밀번호 원문이나 임시 비밀번호를 표시하지 않는다.

적용 역할은 현재 테넌트의 활성 `TenantMembership`을 가진 다음 모든 사용자다.

- `owner`, `admin`, `teacher`, `staff`
- `student`, `parent`

완료 여부는 브라우저가 아니라 `User.first_login_guide_completed_at`에 계정 단위로
저장한다. 따라서 한 기기에서 확인하면 다른 기기나 다른 테넌트 세션에서도 다시
표시하지 않는다.

## 기존 계정과 신규 계정 경계

마이그레이션 `0052_user_first_login_guide_completed_at`은 필드를 추가한 뒤 기존
사용자의 완료 시각을 일괄 기록한다. 기존 운영 계정에는 기능 배포 직후 안내가
갑자기 나타나지 않는다.

마이그레이션 이후 생성되는 사용자는 완료 시각이 `null`이므로 첫 접속 안내 대상이
된다. 계정 생성 서비스나 신규 테넌트 프로비저닝에서 별도 플래그를 설정할 필요가
없다.

## API 계약

`GET /api/v1/core/me/`는 인증된 현재 테넌트의 활성 멤버에게 다음 상태를 추가로
반환한다.

```json
{
  "first_login_guide_required": true
}
```

`POST /api/v1/core/me/first-login-guide/complete/`는 확인 완료 시각을 기록한다.

- 인증과 현재 테넌트의 활성 멤버십이 모두 필요하다.
- 이미 완료된 요청을 반복해도 기존 완료 시각을 유지하는 idempotent 연산이다.
- 다른 사용자 ID를 입력받지 않으므로 다른 계정 상태를 변경할 수 없다.
- 성공 응답은 `first_login_guide_required: false`와 `completed_at`을 반환한다.

프론트 UX와 역할별 이동 경로는
`frontend/docs/FIRST-LOGIN-GUIDE.md`가 소유한다.

## 초기·임시 비밀번호 변경 권장과의 관계

`must_change_password`는 초기·임시 비밀번호 사용자에게 보안 위험과 변경 경로를
안내하는 권장 상태이며 이 안내와 독립적이다. 서버 middleware는 이 값으로 API를
차단하지 않는다.

- 첫 접속 안내 때문에 `must_change_password`를 켜지 않는다.
- `must_change_password=true`이면 현재 화면 위에 변경 권장 모달을 먼저 표시하며,
  위험을 이해하고 나중에 변경하는 선택도 제공한다.
- 변경 및 재로그인 이후에도 안내 완료 시각이 비어 있으면 권유형 안내가
  한 번 표시된다.

## 실패 동작

- 완료 API가 실패하면 완료 시각을 추정하거나 브라우저에 영구 저장하지 않는다.
- 프론트는 안내를 유지하고 재시도 가능한 오류를 표시한다.
- `/core/me/`가 실패하면 기존 인증 장애 처리 계약을 따르며, 안내 상태를
  임의로 완료 처리하지 않는다.

## 검증

```powershell
python -m pytest apps\core\tests\test_first_login_guide.py -v --tb=short -x
python manage.py makemigrations --check --dry-run
python manage.py check
```

프론트 집중 검증:

```powershell
pnpm exec playwright test e2e/auth/first-login-guide.mock.spec.ts --project=chromium --reporter=list
```
