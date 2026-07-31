# 컨테이너 이미지 보안 게이트

운영 후보 이미지는 immutable ECR digest로 식별하며 persistent development보다
먼저 기본 ECR scan을 완료해야 한다. 실행 정본은
`.github/workflows/v1-build-and-push-latest.yml`과
`scripts/v1/ecr-critical-scan-gate.py`다.

## 빌드 입력과 런타임 패키지

- 공통 Python 이미지는 `docker/Dockerfile.base`의 두 stage 모두 같은 upstream
  OCI index digest로 고정한다. 태그가 이동해도 승인되지 않은 OS 변경이 빌드에
  섞이지 않는다.
- Docker Dependabot이 `/docker`를 매주 확인하며, base digest 변경은 일반 PR과
  ECR scan을 다시 통과해야 한다.
- 런타임에는 앱이 실제 사용하는 패키지만 둔다. DB migration과 점검은 Django와
  AWS/RDS readback을 사용하므로 `postgresql-client` CLI는 제거했고, Python
  PostgreSQL 연결에 필요한 `libpq5`는 유지한다.

## Critical 판정

1. 후보 manifest에 `source=built`인 각 digest의 scan 결과가 없으면 CI가
   repository-scoped `ecr:StartImageScan` 권한으로 scan을 호출한다. 재사용
   digest라는 이유로 scan을 건너뛰지 않는다.
2. scan이 `COMPLETE`가 아니거나 finding identity(CVE, package, version)가
   불완전하면 실패 폐쇄한다.
3. 승인되지 않은 Critical은 하나라도 있으면 development/preprod/production으로
   진행하지 않는다. High는 경고와 후속 remediation 대상으로 남긴다.
4. 예외는 `docs/ssot/ecr-critical-risk-acceptance.json`에 repository, CVE,
   package, version, 만료일, Debian tracker와 도달 가능성 근거를 모두 정확히
   적은 항목만 허용한다. wildcard는 없으며 package version이나 CVE가 달라지면
   즉시 실패한다.
5. 만료일 다음 날부터는 scan 전에 전체 게이트가 실패한다. 만료 연장은 새
   vendor 상태와 실제 사용 경로를 다시 검토한 PR로만 가능하다.

현재 한시 항목은 Debian stable에 수정본이 아직 없고 Debian이 `no-dsa`/minor로
분류한 glibc·Mbed TLS finding이다. glibc 취약 native `scanf` 경로와 Mbed TLS
FFDH/TLS-session 경로는 Academy Python 앱의 실행 경로가 아니며, 공개 TLS는 ALB가
종단한다. Mbed TLS는 API/Video/AI 이미지의 미디어 도구 전이 의존성이다. 이
판단은 위험을 삭제하지 않으므로 정확한 버전에서만 2026-08-14까지 유효하다.

불필요한 `postgresql-client`가 끌어오던 Perl Critical은 예외 처리하지 않고
패키지 자체를 제거한다. vendor 수정 base digest가 제공되면 acceptance를
삭제하고 전체 6-image scan과 release 연속성 게이트를 다시 실행한다.

집중 검증:

```powershell
python -m pytest tests/test_ecr_critical_scan_gate.py -q
pwsh scripts/v1/test-workflow-governance-contract.ps1
```
