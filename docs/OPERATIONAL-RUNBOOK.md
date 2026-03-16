# 운영 복구 런북 (Operational Runbook)

## 영상 처리

| 상황 | 명령 | 비고 |
|------|------|------|
| 인코딩 멈춤 (PENDING 상태 오래됨) | `recover_stuck_videos` | PENDING → NEW로 리셋 |
| 인코딩 실패 후 재시도 | `enqueue_uploaded_videos --include-failed` | FAILED 영상 재큐잉 |
| 특정 영상 강제 완료 | `force_complete_videos --tenant-id N` | 수동 완료 처리 |
| Batch 작업 상태 불일치 | `reconcile_batch_video_jobs` | AWS Batch ↔ DB 동기화 |
| 멈춘 작업 진단 | `scan_stuck_video_jobs` | 멈춘 이유 분석 |
| 삭제 영상 정리 (180일) | `purge_deleted_videos` | soft-delete 후 영구 삭제 |

## 메시징 (알림톡/SMS)

| 상황 | 명령 | 비고 |
|------|------|------|
| 템플릿 솔라피 검수 신청 | `submit_all_templates_review` | 미신청 템플릿 일괄 신청 |
| 마스터 템플릿 복사 | `copy_master_template` | 승인 템플릿을 다른 테넌트로 |
| 잔액 설정 | `set_tenant_messaging_credits` | credit_balance 조정 |

## 학생/테넌트

| 상황 | 명령 | 비고 |
|------|------|------|
| 삭제 학생 영구 정리 | `purge_deleted_students` | 30일 경과 학생 영구 삭제 |
| 학부모 계정 누락 | `ensure_parent_accounts_for_students` | 학부모 자동 생성 |
| 테넌트 점검 | `check_tenants` | 모든 테넌트 상태 확인 |
| 테넌트 오너 확인 | `list_tenant_owners` | 오너 계정 목록 |
| 비밀번호 리셋 | `fix_user_password` | 수동 비밀번호 변경 |

## 배포/인프라

| 상황 | 명령 | 비고 |
|------|------|------|
| API 서버 롤백 | ECR sha 태그 re-tag → ASG refresh | SHA 태그로 이전 이미지 복원 |
| 헬스체크 | `/healthz` (liveness), `/health` (readiness) | ALB + 수동 확인 |
| 워커 상태 | SQS 큐 depth + DLQ 확인 | CloudWatch or AWS Console |

## Sentry

| 상황 | 확인 방법 | 비고 |
|------|-----------|------|
| 프론트 에러 모니터링 | Sentry 대시보드 (frontend 프로젝트) | tenant 태그로 필터 |
| 백엔드 에러 모니터링 | Sentry 대시보드 (backend 프로젝트) | correlation_id로 추적 |
| 검증 | DEBUG 모드에서 `/sentry-test/` 호출 | 프로덕션에서는 404 반환 |
