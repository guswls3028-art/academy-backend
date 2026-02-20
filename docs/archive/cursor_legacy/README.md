# docs_cursor — Cursor 작업용 SSOT (참고용)

> **신규 문서 작성 위치**: 이 폴더가 아님. **docs/SSOT_MMDD/cursor_only/** 에만 새 문서를 만들 것. (MMDD = 오늘 날짜 4자리. 규칙: `.cursor/rules/ssot-folder-structure.mdc`)

이 폴더는 기존 문서 참고용입니다. Cursor(AI)가 작업할 때 읽을 수 있도록 정리된 문서 모음이며, 추측·가정 없이 **실제 코드·설정 기준**으로만 기술합니다.

## 문서 목록 (읽는 순서 권장)

| 문서 | 내용 | 작업 시 참고 |
|------|------|--------------|
| [01-core-tenant-program.md](01-core-tenant-program.md) | 테넌트 resolve, Program, 권한, bypass | core/테넌트/멀티테넌트 관련 수정 시 |
| [02-core-apis.md](02-core-apis.md) | core URL·View·권한·요청/응답 DTO | API 추가·수정, dev_app 연동 |
| [03-settings-env.md](03-settings-env.md) | CORS, CSRF, ALLOWED_HOSTS, DB, TENANT_BYPASS, 주요 ENV | 배포·환경·CORS/도메인 이슈 |
| [04-deployment.md](04-deployment.md) | 배포 순서, Docker 이미지, EC2, 스크립트 경로 | 배포·인프라 작업 |
| [05-conventions.md](05-conventions.md) | 문서/코드 규칙, core 봉인, 추측 금지 | 모든 작업 전 참고 |
| [06-front-infra-and-excel.md](06-front-infra-and-excel.md) | 프론트·인프라 계약 요약, 백엔드 엑셀 파싱 사실 (상세는 academyfront/06-implemented-features) | CORS·도메인·엑셀 에러 시 참조 |
| [07-staffs-api.md](07-staffs-api.md) | staffs 도메인 API: work-types, staff-work-types (POST body: staff, work_type_id), Staff | 시급태그·직원 API 연동·수정 시 |
| [08-worker-deployment-and-test.md](08-worker-deployment-and-test.md) | 워커 배포 환경(Messaging/Video/AI, ASG, SSM env), 로컬·배포 후 테스트 방법 | 워커 테스트·배포 시 |
| [10-deploy-commands-cheatsheet.md](10-deploy-commands-cheatsheet.md) | 배포 명령어 모음 (풀배포, 워커 리프레시, IAM Deny, ASG 확인 등) | 배포·운영 시 복붙용 |
| [11-worker-self-stop-root-cause.md](11-worker-self-stop-root-cause.md) | Worker self-stop 루트캐우스 분석, IAM ec2:StopInstances 차단 방법 | 껐다 켜짐 루프 진단·해결 시 |
| [12-excel-parsing-improvements.md](12-excel-parsing-improvements.md) | 엑셀 파싱 개선 (헤더 별칭, 행 판별, parent_phone 필수) | 엑셀 업로드 관련 수정 시 |
| [13-excel-parsing-final-design.md](13-excel-parsing-final-design.md) | 엑셀 파싱 최종 설계 (Parent Phone Mandatory + AI Hybrid) | 설계·운영 정책 참조 시 |
| [14-solapi-check-guide.md](14-solapi-check-guide.md) | 솔라피 콘솔 확인 (발신번호·잔액·IP) | 메시지 발송 실패 시 |
| [15-messaging-worker-and-message-flow.md](15-messaging-worker-and-message-flow.md) | Messaging Worker · message_mode · 자동발송 · API | 메시징 수정·운영 시 |
| [16-verification-report-0218.md](16-verification-report-0218.md) | 검증 보고서 (0218) 문서·코드 대조, API 정합성, 빌드 | 변경사항 점검·배포 전 확인 |
| [17-project-structure-and-doc-verification.md](17-project-structure-and-doc-verification.md) | 프로젝트 구조, 문서–코드 검증 (구조 파악·일치 여부) | 신규 참여·문서 정합성 점검 시 |
| [18-messaging-runtime-checklist.md](18-messaging-runtime-checklist.md) | 메시징 **런타임 4계층** 진단 (API→SQS→Worker→Solapi), 수집할 3가지 | 메시지 안 갈 때 진단용 |
| [19-messaging-send-test-steps.md](19-messaging-send-test-steps.md) | 메시지 **발송 테스트 절차** (명령어·확인만, 추측 없음) | 발송 E2E 테스트 시 |
| [20-messaging-4layer-code-analysis.md](20-messaging-4layer-code-analysis.md) | 메시징 **4계층 코드 분석** (파일·라인·코드 근거, 막히는 조건) | Messaging 미동작 진단 시 |

## 날짜별 스냅샷

- **docs/SSOT_0217/**: 2025-02-17 현시점 전체 문서 스냅샷 (cursor_* + 배포·설계·운영·0216·adr). 이후 SSOT_0218, SSOT_0219 … 생성하여 섞이지 않게 관리.

## 원본 문서 위치

- **Core 봉인(헌법)**: `apps/core/CORE_SEAL.md`
- **배포 상세**: `docs/배포.md`
- **문서 인덱스**: `docs/README.md`

## 규칙

- **문서만으로 판단**: 이 폴더 + 위 원본만 보고도 구현/수정이 가능해야 함.
- **코드가 진실**: 문서와 코드 불일치 시 코드가 우선. 문서를 코드에 맞게 수정할 것.
- **추측 금지**: 문서에 없는 동작은 코드/설정을 직접 확인한 뒤 반영할 것.
