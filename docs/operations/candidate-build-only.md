# PR 후보 이미지 빌드 전용 경로

실행 정본은 `.github/workflows/candidate-build-only.yml`과
`scripts/v1/candidate_build_only.py`다. 이 경로는 열린 동일 저장소 PR의 정확한
head SHA를 검증용 ECR 이미지로 만드는 운영자 수동 작업이다. 정식 릴리스 후보,
production manifest, `latest`, DB, EC2, SSM, IAM, 배포는 변경하지 않는다.

## 입력과 신뢰 경계

릴리스 담당자가 **현재 main**의 `Candidate PR build only`를 수동 실행하며
`pr_number`와 40자리 `source_sha`를 입력한다. Controller는 GitHub API로 현재
main SHA, 열린 같은 저장소 PR의 head/base, 정확한 PR head와 현재 main의
`Backend Quality Gate` 최신 pull_request 실행 성공을 확인한다. 정적·Django·PG
계약 잡과 native 감지 잡은 성공해야 하며, native 이미지 잡만 변경 감지에 따라
성공 또는 건너뜀을 허용한다. PR이 품질 게이트 workflow 자체를 수정했으면
중단한다. 게이트는 PR head를 현재 main과 합친 revision에서 실행되므로 해당
실행의 PR base SHA도 현재 main과 일치해야 한다. 환경 승인 대기 또는 장시간
빌드 뒤 PR/CI/main이 바뀌면 권한 작업과 최종 readback에서 다시 중단한다.

PR 코드는 자격 증명을 보존하지 않는 ARM64 작업에서만 Docker build 입력으로
사용한다. 이 작업은 AWS 권한, `production` 환경, 공유 production BuildKit
캐시에 접근하지 않는다. 각 이미지는 별도 ARM64 runner에서 빌드해 압축 Docker
archive와 SHA-256·이미지 config ID·OS/architecture·실행 ID/attempt 영수증을
1일 artifact로 남긴다. 권한 작업은 main controller만 checkout하며 Dockerfile,
PR 스크립트, 이미지 entrypoint를 실행하지 않는다. archive의 경로, 멤버 수,
팽창 크기, 단일 이미지 태그, config ID와 SHA-256을 확인한 뒤 `docker load`한다.
기존 `production` 환경 OIDC 역할에 ECR repository 범위의 inline session policy를
적용한다. 이 정책은 발급된 세션의 권한을 줄이지만 별도 IAM trust boundary는
아니다. 따라서 main workflow와 Docker/archive parser의 보안이 여전히 중요하다.

공통 base와 Messaging은 마지막 **성공·완전한** production manifest의 immutable
tag/digest를 출처로 삼는다. base 입력은 Dockerfile, native-security,
`requirements/common.txt`, constraints, `.dockerignore`와 이전 이미지의 빌드
commit을 PR head와 비교한다. Messaging은 추가로 해당 Dockerfile, requirements,
`academy/`, `apps/`, `libs/`, `manage.py` 전체를 비교한다. base가 달라지면
Messaging도 재빌드한다. 재사용하는 경우 ECR에서 원본 tag의 digest를 다시
읽어 manifest와 같아야만 base를 export하고 Messaging을 유지한다. 증명이
불가능하면 안전하게 중단하거나 PR 소스에서 다시 빌드한다.

## 결과와 사용 범위

API, AI CPU, Tools와 필요한 base/Messaging만 기존 ECR 저장소에
`qa-pr-<PR>-<headSHA>-run-<runID>-<attempt>` 고유 태그로 푸시한다. 각 태그의
저장소 불변성·scanOnPush를 readback하고, 기존 ECR Critical/High 정책으로
완료 스캔을 통과해야 한다. 최종 작업은 다섯 이미지의 태그와 digest를 전부
재조회한 뒤에만 `candidate-complete-<runID>-<attempt>` QA 전용 receipt를
발행한다. 개별 push 성공 또는 부분 receipt는 완전한 이미지 세트가 아니다.
Video worker는 이 작업의 빌드 대상이 아니며, 이 receipt는 여섯 이미지
production 후보 manifest나 정식 릴리스 게이트를 대체하지 않는다. 후속 QA는
해당 receipt의 정확한 digest만 사용하고 정식 배포는 기존 immutable 후보 →
persistent development → preprod → production 절차를 별도로 통과한다.

## 실패와 정리

- PR close/head 변경, main 변경, CI 재실행·실패·base 불일치, manifest 출처 또는
  ECR digest 불일치, artifact 검증/공간 부족, scan 실패는 중단 조건이다. 새 main과
  head에 대해 새 run을 시작한다. artifact는 1일 후 만료되므로 만료 후에는
  소스/이미지 증명에 이전 artifact를 사용하지 않는다.
- 부분 push가 남으면 그 run의 `qa-pr-*` 태그와 각 digest를 정확히 열거하고
  production `latest`·성공 manifest·다른 QA 참조가 없는지 확인한다. 릴리스
  담당자가 대상 repository/tag/digest와 개수를 고정한 뒤에만 해당 태그를
  정리하고 삭제 후 readback한다. 다른 태그나 untagged image를 묶어서 지우지
  않는다. 완료 receipt가 없으면 부분 이미지를 QA 후보로 사용하지 않는다.
- ARM64 hosted runner의 14 GB 저장 공간에서 AI 모델·base·archive가 동시에
  커질 수 있다. 빌드 실패는 비용/용량 신호이며 production runner나 자격
  증명 작업으로 PR Docker build를 옮겨 해결하지 않는다. 더 큰 격리 runner가
  필요하면 별도 운영 변경으로 검토한다.
