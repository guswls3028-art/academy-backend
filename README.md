# Academy Backend

학원 운영 데이터와 API를 제공하는 Django·DRF 백엔드다. PostgreSQL은 테넌트별 업무 데이터를 저장하고, Cloudflare R2는 파일을 보관한다. 전체 문서는 [백엔드 문서 색인](docs/README.md)에서 찾을 수 있다.

## 작업별 문서

| 작업 | 확인할 문서 |
|---|---|
| 교직원·학생 파일의 소유권, 삭제 및 복구 확인 | [인벤토리 저장소](docs/domain/inventory-storage.md) |
| 매치업 원본, 수동 편집, 보고서 참조와 분석 산출물 확인 | [매치업 데이터 수명주기](docs/domain/matchup.md) |
| 로컬 용량 확보 및 작업 종료 후 산출물 정리 | [디스크 용량](docs/operations/concurrent-codex-sessions.md#local-disk-capacity) · [세션 산출물 보관 기준](docs/operations/concurrent-codex-sessions.md#session-output-lifecycle) |
| 봉인한 릴리스의 변경 내역 확인 | [릴리스 노트 색인의 CURRENT 행](docs/releases/README.md) |
| 배포된 리비전과 이미지 확인 | [배포 매니페스트](docs/reports/release-manifest.latest.json) · [배포 방식과 검증 절차](docs/operations/deployment-modes.md) |
| 인프라 구성과 실행 파라미터 확인 | [배포 아키텍처](docs/infrastructure/deployment-architecture.md) · [실행 파라미터](docs/ssot/params.yaml) |
| 정식 수동 배포 절차 확인 | [운영 절차](docs/operations/formal-deploy.md) · [실행 스크립트](scripts/v1/deploy.ps1) |

## 실행 경계

API와 워커는 Linux ARM64 컨테이너로 실행한다. 영상 인코딩은 AWS Batch가 담당한다. 메시징, AI, 문서 도구는 각각 전용 SQS 큐와 워커에서 처리한다. 런타임 의존성 버전은 [제약 파일](requirements/constraints.txt)에서 확인할 수 있다.
