# 운영 가이드 (체크리스트·흐름·복구·비용)

배포는 [500명 스타트 가이드](SSOT_0215/AWS_500_START_DEPLOY_GUIDE.md), 배포 전 체크는 [cursor_docs/500_DEPLOY_CHECKLIST.md](cursor_docs/500_DEPLOY_CHECKLIST.md), 아키텍처는 [ARCHITECTURE_AND_INFRASTRUCTURE.md](ARCHITECTURE_AND_INFRASTRUCTURE.md) 참고.

---

## 1. 배포 전 체크리스트

### 1.1 Docker 빌드

- **순서**: `academy-base` → api → video-worker → ai-worker → messaging-worker`. base 없으면 실패.
- **한 번에**: `.\docker\build.ps1` (Windows) / `./docker/build.sh` (Linux)
- **자주 나는 오류**: 베이스 미빌드 → base 먼저. `COPY requirements/` 실패 → 컨텍스트를 프로젝트 루트(`.`)에서 실행. `.dockerignore`에 `.env`, `venv/` 포함 확인.

### 1.2 API 기동 전

1. **마이그레이션**: `docker compose exec api python manage.py migrate` (자동 아님)
2. **필수 env**: `SECRET_KEY`, `DB_*`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_AI_BUCKET`, `R2_VIDEO_BUCKET`, `R2_EXCEL_BUCKET`, SQS 큐 이름, `INTERNAL_WORKER_TOKEN`
3. **헬스**: `GET /health` → 200, `database: "connected"`

### 1.3 워커

- **설정**: `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker`
- **공통**: DB, SQS 큐 이름, R2(엑셀용 `R2_EXCEL_BUCKET` 포함). API 연동 시 `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`
- **실패 시**: SQS 권한/리전, API와 동일한 `R2_EXCEL_BUCKET` 사용 여부 확인

### 1.4 검증 스크립트

```bash
python scripts/deployment_readiness_check.py --docker --local   # 로컬만
python scripts/deployment_readiness_check.py --docker           # 실제 DB·SQS 연동
```

---

## 2. 엑셀 수강등록 처리 흐름 (코드 기반)

- **API**: 동기 처리 없음. 파일 수신 → R2(`R2_EXCEL_BUCKET`) 업로드 → SQS에 `job_type: "excel_parsing"` 등록 → `202` + `job_id` 반환.
- **프론트**: `GET /api/v1/enrollments/excel_job_status/<job_id>/` 폴링 → `status === "DONE"` 시 결과 표시.
- **워커**: SQS 수신 → R2에서 파일 다운로드 → `ExcelParsingService.run()` → `lecture_enroll_from_excel_rows()` (도메인) → 완료 시 결과 저장, R2 객체 삭제.

**관련 코드**: API `apps/domains/enrollment/views.py` (lecture_enroll_from_excel, excel_job_status), 워커 `apps/worker/ai_worker/ai/pipelines/excel_handler.py`, 비즈니스 `apps/domains/enrollment/services.py`.

---

## 3. 학생 도메인 버그 복구

### 3.1 삭제된 학생 중복 (이름+학부모전화 동일 다건)

- **증상**: 삭제된 학생 목록에 동일 이름·학부모전화 여러 건.
- **고객**: 관리자 → 학생 → 삭제된 학생 → **중복 검사** → **중복 정리**.
- **서버**:
  - 점검: `python manage.py check_deleted_student_duplicates` (예행: `--dry-run`)
  - 정리: `python manage.py check_deleted_student_duplicates --fix`
- **정리 규칙**: (tenant, 이름, 학부모전화) 동일 그룹에서 `deleted_at` 가장 오래된 1명만 남기고 나머지 영구 삭제.

### 3.2 30일 지난 삭제된 학생 영구 삭제

```bash
python manage.py purge_deleted_students --dry-run   # 대상 확인
python manage.py purge_deleted_students             # 실행
```

---

## 4. R2 요약 (구현 기준)

| 항목 | 현재 구현 |
|------|-----------|
| 엑셀 업로드(수강등록) | 처리 완료 시 워커에서 R2 객체 **즉시 삭제** |
| 엑셀 내보내기 | `exports/{tenant_id}/{job_id}_{filename}` 업로드 후 삭제/Lifecycle 없음 → 24h Lifecycle 또는 cron 정리 권장 |
| 영상 원본(Raw) | 인코딩 후에도 삭제 안 함 → 30일 후 삭제/이관 정책 권장 |
| HLS | 무한 유지. 삭제 시 `delete_r2_legacy --bucket video --prefix "tenants/…/video/hls/{id}"` 수동 사용 가능 |

버킷: `academy-ai`, `academy-video`, `academy-excel`, `academy-storage` (설정: `apps/api/config/settings/base.py`, `.env.example`).

---

## 5. 비용 요약 (월, USD)

| 규모 | 예상 월 비용 |
|------|----------------|
| 500 DAU | Compute·ALB·RDS·R2·SQS·CloudWatch 등 **~$108–130** |
| 10k DAU | **~$420–670** (RDS Multi-AZ, 워커 확장 등) |

비용 절감: SQS Long Polling 20초, EC2 Self-Stop(Video/AI), R2 사용. 상세는 500START 가이드 및 AWS Budgets 알림 설정 참고.

---

## 6. 배포 후 할 일 (오픈 전·확장 시)

| 시점 | 할 일 |
|------|-------|
| **실제 오픈 전** | ALB 생성, Target Group health check `/health`, ACM 443, 80→443 리다이렉트 (가이드 "오픈 전 필수"). 8000 직접 노출은 테스트용만. |
| **트래픽 증가 시** | Video/AI 워커 Auto Scaling(Lambda+CloudWatch 또는 ASG) 검토. 첫 달은 수동 기동으로 충분. |
| **운영 안정화** | CloudWatch 대시보드(CPU·Memory·RDS·SQS) 권장. DLQ·Self-Stop 동작 1회 확인. |
