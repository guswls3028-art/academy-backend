# Project Work Queue

## Pending Tasks

- [ ] 2. user ↔ tenant ↔ role 연결 검사
- [ ] 3. community / QnA / notices tenant isolation 검사
- [ ] 4. student ↔ teacher 파이프라인 검사
- [ ] 5. sessions / attendance / grades / clinic 검사
- [ ] 6. notifications / counts / dashboard aggregate tenant 검사
- [ ] 7. video / upload / processing / player 검사
- [ ] 8. cache / storage / object path / key namespace 검사
- [ ] 9. workers / messaging / batch / scheduled jobs tenant 검사
- [ ] 10. deploy / verify / run-remote / env / ECR / instance refresh 검사
- [ ] 11. 나머지 UI·기능 점검

## In Progress

- (없음)

## Completed

- 전체 분석/계획 보고서 작성 및 제출 (전수점검-분석-계획-보고서.md)
- 이미 수정된 5건 재확인: progress_views tenant 필터, run-api-management-remote /opt/api.env, resolver 규칙3 Host 제한, is_reservation_cancelled tenant_id, 배포.md §1.1·§10 — 코드 기준 확인 완료
- 1. tenant resolver / middleware / auth binding 전수 검사 — resolver 규칙1·2·3·bypass, middleware bypass·실패 시 즉시 실패·finally clear, auth tenant 필수·user_get_by_tenant_username·tenant_id None 거부 확인 → 정상
