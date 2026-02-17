# 엑셀 일괄 등록 실패 시 확인

## 흐름

1. 프론트: POST `/api/v1/students/bulk_create_from_excel/` (파일 + initial_password)
2. API: R2에 엑셀 업로드 → excel_parsing job 생성 → 202 { job_id }
3. 프론트: 1초마다 GET `/api/v1/jobs/<job_id>/` 폴링 → DONE/FAILED 시 완료 표시
4. AI 워커: SQS에서 job 가져와 엑셀 파싱 → 학생 생성

## 실패 시 확인 순서

### 1. 업로드 단계에서 실패 (모달에서 바로 에러 메시지)

- **API 서버 .env**에 R2 설정 있는지 확인:
  - `R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`
  - `R2_EXCEL_BUCKET` 또는 `EXCEL_BUCKET_NAME` (없으면 academy-excel 사용)
- R2 버킷이 실제로 존재하고, 위 키로 접근 가능한지 확인.
- 브라우저 개발자 도구 → Network: `bulk_create_from_excel` 요청이 **400/500** 이면 응답 본문에 `detail` 등 에러 내용 있음.

### 2. 작업 상태 조회 502 (업로드는 됐는데 “실패”로 보임)

- GET `/api/v1/jobs/<job_id>/` 가 502 나오면 폴링이 실패해 완료/실패를 못 보여줌.
- Cloudflare SSL **Flexible**, API 서버 **80/8000** 열림, nginx → 8000 프록시 확인 (이전 502 점검 참고).
- 브라우저 Network에서 `jobs/` 요청이 **502** 인지 확인.

### 3. 워커가 job을 안 가져감 (job은 PENDING 그대로)

- **AI 워커** 인스턴스가 1대 이상 떠 있는지 (ASG desired >= 1).
- SQS `academy-ai-jobs-lite` / `academy-ai-jobs-basic` 에 메시지가 쌓이는지.
- 워커 EC2/컨테이너 로그에 excel_parsing 처리 로그 또는 에러가 있는지.

### 4. 워커가 처리했는데 FAILED

- GET `/api/v1/jobs/<job_id>/` 응답에 `status: "FAILED"`, `error_message` 있음.
- 워커 로그에서 해당 job_id / excel_parsing 예외 확인.
- 워커 .env에도 R2 설정 필요 (동일 버킷에서 엑셀 다운로드).

## 한 줄 요약

업로드 즉시 에러 → R2 설정/버킷. 업로드 후 진행만 안 보임 → jobs 502 또는 AI 워커 미기동. 완료됐는데 실패 표시 → job 상태가 FAILED면 error_message 확인.
