# Project Work Queue

## Pending Tasks

- (없음)

## In Progress

- (없음)

## Completed

- 전체 분석/계획 보고서 작성 및 제출 (전수점검-분석-계획-보고서.md)
- 이미 수정된 5건 재확인: progress_views tenant 필터, run-api-management-remote /opt/api.env, resolver 규칙3 Host 제한, is_reservation_cancelled tenant_id, 배포.md §1.1·§10 — 코드 기준 확인 완료
- 1. tenant resolver / middleware / auth binding 전수 검사 — 정상
- 2. user ↔ tenant ↔ role 연결 검사 — core/student_app/enrollment permissions에서 request.tenant·membership_exists·tenant 필터 사용 확인, 정상
- 3. community / QnA / notices tenant isolation 검사 — post_selector 전부 tenant 인자·filter(tenant=tenant), PostViewSet get_queryset/create tenant 또는 request_student.tenant 사용, create 시 tenant 없으면 403, 정상 (단 create에서 request_student.tenant 폴백은 동일 사용자 소속이지만 원칙상 fallback에 해당, resolver가 정상이면 request.tenant 있음)
- 4. student ↔ teacher 파이프라인 검사 — students/staffs/lectures/attendance/enrollment/clinic views 전반 request.tenant·repo filter_tenant·get_*_tenant 사용, lecture.tenant_id 검증, 정상
- 5. sessions / attendance / grades / clinic 검사 — lectures Session lecture__tenant, attendance/enrollment/clinic tenant 인자·filter(tenant=tenant), results permissions is_effective_staff(tenant), student_app results Enrollment.filter(student=student)로 학생 범위, 정상
- 6. notifications / counts / dashboard aggregate tenant 검사 — StudentDashboardView 빈 목록 반환(tenant 쿼리 없음), messaging views tenant 필터, cache key에 tenant_id 포함(pw_reset, idempotency), 정상
- 7. video / upload / processing / player 검사 — progress_views request.tenant·Video session__lecture__tenant, video_views session get_by_id_with_lecture_tenant 후 request_tenant vs tenant 검증, redis_status_cache·encoding_progress 키 tenant:{id}:video:..., 정상
- 8. cache / storage / object path / key namespace 검사 — Redis video/AI job 키 tenant prefix, pw_reset/staff_export/attendance_export idempotency·file_key에 tenant.id, core tenant-logos/{tenant_id}, excel_export exports/{tenant_id}/..., 정상
- 9. workers / messaging / batch / scheduled jobs tenant 검사 — sqs_main tenant_id 전달·is_reservation_cancelled(tenant_id=), video encoding tenant_id·VideoTranscodeJob tenant_id, AI job tenant_id·redis key tenant prefix, 정상
- 10. deploy / verify / run-remote / env / ECR / instance refresh 검사 — deploy.ps1·api.ps1 SSM→/opt/api.env, run-api-management-remote /opt/api.env·ECR full URI, run-qna-e2e-verify InService 우선·PATH·/opt/api.env·ALB·verify_qna_e2e, 정상
- 11. 나머지 UI·기능 점검 — AdminRouter/StudentRouter 라우트·results/student_app 결과 조회 학생 범위, UI는 API 호출 시 tenant context 유지 가정, 정상
