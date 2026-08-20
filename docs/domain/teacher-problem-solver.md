# 강사 AI 문제 풀이·해설 (Beta)

## 목적과 범위

강사가 문제 사진 한 장을 올리면 AI가 정답, 단계별 해설, 정답 확인
근거의 초안을 만든다. 결과는 수업 자료에 자동 반영하지 않는
`teacher_review_required` 제안이며, 강사가 원문과 대조해 검수한 뒤
사용해야 한다.

현재 제공 대상은 테넌트가 확정된 인증 강사·직원이다. 학생 앱에는 이
기능을 노출하지 않는다. 프론트엔드 진입점과 상호작용 계약은
[frontend 강사 도구 문서](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/TEACHER-TOOLS.md)가
소유한다.

## API와 처리 흐름

| 단계 | 계약 |
|------|------|
| 작업 생성 | `POST /api/v1/tools/problem-solver/jobs/` |
| 입력 | `multipart/form-data`: `image`, 선택 `subject`, 필수 `privacy_confirmed=true` |
| 생성 응답 | HTTP 202, `{"job_id": "...", "status": "PENDING"}` |
| 상태 조회 | `GET /api/v1/tools/problem-solver/jobs/{job_id}/` |
| 완료 결과 | `answer`, `explanation`, `answer_check`, `confidence`, `subject`, `review_status=teacher_review_required` |

1. API가 인증, 테넌트, 직원 권한과 개인정보 확인을 먼저 검증한다.
2. 이미지의 실제 형식과 크기를 검증한 뒤
   `tenants/{tenant_id}/tools/problem-solver/tmp/{uuid}/` 아래 R2 임시
   객체로 저장한다.
3. `teacher_problem_explanation` AI 작업을 `basic` tier로 생성하고 AI
   큐에 발행한다. DB 커밋 이후에만 SQS 발행이 일어난다.
4. AI worker가 테넌트 경로를 다시 검증하고 이미지를 임시 디스크로
   내려받아 문제를 전사한 뒤 풀이·해설 초안을 생성한다.
5. 종단 상태에서 R2 원본과 로컬 임시파일을 정리하고, DB 작업
   payload에서 `source_image_key`를 제거한다.
6. 상태 API는 허용된 결과 필드와 일반화된 오류만 반환한다. 결과는
   별도 AI 결과 레코드에 남지만 학생 답안, 시험 정답, 수업 자료 등
   canonical 제품 데이터에는 쓰지 않는다.

## 입력 규칙

- 허용 형식: 실제 이미지 형식이 JPEG, PNG, WEBP인 파일
- 최대 파일 크기: 12MB
- 최소 해상도: 짧은 변 320px
- 최대 해상도: 40,000,000 pixels
- 과목: 선택 입력, 최대 40자
- 개인정보 확인: 학생 이름·연락처 등 개인정보가 보이지 않는
  사진임을 사용자가 확인해야 한다.
- 같은 테넌트·사용자는 생성 시작 10초 동안 중복 생성이 잠기며,
  중복 요청은 HTTP 429를 반환한다.

클라이언트 검증은 사용성을 위한 선검사일 뿐이다. 위 규칙과 권한의
최종 소유자는 백엔드 API다.

## 권한과 데이터 경계

- `IsAuthenticated`와 `TenantResolvedAndStaff`를 모두 통과해야 한다.
  학생 멤버십은 거부한다.
- 작업 조회는 URL의 작업 ID뿐 아니라 현재 테넌트와
  `request_user_id`가 모두 일치해야 한다. 불일치하거나 없는 작업은
  HTTP 404로 처리해 존재 여부를 노출하지 않는다.
- 업로드 키와 worker 입력 키는 현재 테넌트의 정확한 임시 prefix로
  시작해야 한다. 다른 테넌트의 키는 처리하거나 삭제하지 않는다.
- 생성·상태 응답은 모두 `Cache-Control: no-store`다.
- API 응답에는 R2 키, provider 오류, 내부 예외, 모델 프롬프트를
  포함하지 않는다.
- 자동 생성 결과는 검토 제안이다. 별도의 명시적 제품 흐름 없이
  승인 데이터로 승격하거나 학생에게 공개하지 않는다.

## 실패, 재시도, 정리

- 업로드 또는 작업 발행 실패는 일반화된 503 메시지로 반환한다.
- SQS 발행 전후 실패, worker 성공·실패, 종단 callback 경로 모두
  소유한 R2 원본을 정리한다. 정리는 테넌트 prefix 검증을 통과한
  객체에만 수행한다.
- worker 내부 오류는 구조화 로그에 남기되 상태 API에는
  `풀이 초안을 만들지 못했습니다...` 일반 메시지만 노출한다.
- 상태 조회가 일시 실패해도 기존 작업은 유지된다. 클라이언트는 같은
  작업 ID를 다시 조회하며 새 AI 작업을 만들지 않는다.
- 최종 상태는 `DONE`, `FAILED`, `REJECTED_BAD_INPUT`,
  `FALLBACK_TO_GPU`, `REVIEW_REQUIRED` 중 하나다.

## 구현과 검증

주요 소유 코드:

- `apps/domains/tools/problem_solver/views.py`
- `academy/application/use_cases/ai/pipelines/teacher_problem_explanation.py`
- `apps/domains/ai/gateway.py`
- `academy/framework/workers/ai_sqs_worker.py`
- `academy/adapters/db/django/repositories_ai.py`

집중 회귀:

```powershell
python -m pytest apps/domains/tools/problem_solver/tests.py -q
python manage.py check --settings apps.api.config.settings.test
python -m ruff check apps/domains/tools/problem_solver academy/application/use_cases/ai/pipelines/teacher_problem_explanation.py
```

집중 테스트는 인증·학생 거부, 요청자/테넌트 격리, 결과 필드
whitelist, provider 오류 은닉, 발행/worker 정리, 교차 테넌트 키
보호를 포함해야 한다. 운영 확인은 합성 비개인정보 이미지만 사용하고
완료 후 AI 큐·DLQ, worker warm baseline/health, API/DB health를 함께
확인한다.
