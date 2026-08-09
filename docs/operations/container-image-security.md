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
  ECR scan을 다시 통과해야 한다. Python 3.11 minor line을 유지하고 3.12+
  전환은 별도 호환성 검증 없이는 자동 제안하지 않는다.
- 런타임에는 앱이 실제 사용하는 패키지만 둔다. DB migration과 점검은 Django와
  AWS/RDS readback을 사용하므로 `postgresql-client` CLI는 제거했고, Python
  PostgreSQL 연결에 필요한 `libpq5`는 유지한다.
- builder의 Python 패키지는 최종 `appuser` 소유권을 지정한
  `COPY --chown`으로 runtime에 한 번만 기록한다. 앱 소스도 같은 방식으로
  복사하며 별도 `RUN chown -R`을 두지 않는다. 별도 chown 레이어는 같은 파일
  바이트를 이미지에 다시 저장해 ECR 크기와 cold pull을 늘리기 때문이다.
- 서비스 Dockerfile은 apt/Python requirements를 `academy/`, `apps/`, `libs/`
  소스보다 먼저 설치한다. 코드 변경은 entrypoint 검증과 소스 레이어만
  무효화하며, requirements가 그대로면 의존성 설치 캐시를 재사용한다.
- 시스템 FFmpeg는 실제 변환을 수행하는 격리된 AWS Batch Video 이미지에만 둔다.
  Debian stable FFmpeg에 수정 패키지가 없는 보안 결함은 High 상한을 올려
  넘기지 않는다. Video 이미지는 공식 FFmpeg GitHub mirror의 전체 commit SHA를
  고정하고 checkout SHA를 빌드 중 재검증한 뒤 source build를 사용한다. 현재
  `db05df9d135fb56a4babb836d5e9f5c1d984e087`은
  CVE-2026-70628과 CVE-2026-70632 수정을 모두 포함한다. 최종 이미지에는
  Debian `ffmpeg`/`libav*` 패키지를 넣지 않고, 고정 빌드의 `ffmpeg`와
  `ffprobe`, H.264 `libx264`, AAC, HLS 실제 변환 smoke를 이미지 빌드에서
  통과시킨다. checkout한 전체 SHA는 설치 디렉터리의
  `academy-source-commit` 마커에 기록하고 최종 stage에서 다시 대조한다.
  FFmpeg가 shallow checkout SHA를 자체 version 문자열에 노출하는지에는
  의존하지 않는다. Video source build만 공개 저장소용 GitHub
  `ubuntu-24.04-arm`에서 네이티브로 수행하며, 다른 runtime 이미지는 기존
  x64 runner와 QEMU 경계를 유지한다. commit을 바꿀 때는 두 수정의 ancestry,
  전체 SHA, 설치 마커, HLS smoke,
  ECR 완료 스캔과 기존 High 상한 비증가를 함께 확인한다. Debian FFmpeg와
  전이 패키지를 제거한 뒤 Video 이미지의 High 상한은 공통 base와 같은 8로
  즉시 낮추며, 이 수치를 넘는 후보는 다시 실패 폐쇄한다.
  API의 upload-complete probe는 실패 허용 보조 검사이고 Video worker가 최종 검증과
  변환을 소유한다. AI frame extraction은 OpenCV wheel에 포함된 FFmpeg 지원을 쓰며,
  wheel이 그 기능을 잃으면 AI 이미지 빌드가 즉시 실패한다. AI와 Video 런타임은
  GUI가 없는 `opencv-python-headless`를 사용하므로 system `libglib2.0-0`을 별도
  설치하지 않는다. OpenCV upstream의 headless 계약, 이미지 import/FFmpeg smoke,
  완료된 ECR scan이 이 제거의 호환성과 보안 경계를 함께 봉인한다.

## Critical 및 High 판정

1. 후보 manifest에 `source=built`인 각 digest의 scan 결과가 없으면 CI가
   repository-scoped `ecr:StartImageScan` 권한으로 scan을 호출한다. 재사용
   digest라는 이유로 scan을 건너뛰지 않는다. ECR이 동일 digest scan quota가
   이미 소비됐다고 응답해도 기존 scan의 `COMPLETE` readback은 끝까지 요구한다.
2. scan이 `COMPLETE`가 아니거나 finding identity(CVE, package, version)가
   불완전하면 실패 폐쇄한다.
3. 승인되지 않은 Critical은 하나라도 있으면 development/preprod/production으로
   진행하지 않는다.
4. 예외는 `docs/ssot/ecr-critical-risk-acceptance.json`에 repository, CVE,
   package, version, 만료일, Debian tracker와 도달 가능성 근거를 모두 정확히
   적은 항목만 허용한다. wildcard는 없으며 package version이나 CVE가 달라지면
   즉시 실패한다.
5. 만료일 다음 날부터는 scan 전에 전체 게이트가 실패한다. 만료 연장은 새
   vendor 상태와 실제 사용 경로를 다시 검토한 PR로만 가능하다.
6. High는 `docs/ssot/ecr-high-risk-baseline.json`의 repository별 상한과 비교한다.
   후보의 수가 상한을 하나라도 넘으면 실패 폐쇄한다. 패키지 제거 또는 vendor
   수정으로 실제 수가 줄면 운영 scan readback에 맞춰 상한도 낮춘다. 아직 수정본이
   없는 Debian finding은 상한 이하에서만 추적되며 새 High가 조용히 유입될 수 없다.

현재 Critical 한시 항목은 Debian stable에 수정본이 아직 없거나 Debian이
`no-dsa`/minor로 분류한 glibc·Mbed TLS·Perl finding이다. glibc 취약 native
`scanf` 경로와 Mbed TLS FFDH/TLS-session 경로는 Academy Python 앱의 실행
경로가 아니며, 공개 TLS는 ALB가 종단한다. Mbed TLS는 API/Video/AI 이미지의
미디어 도구 전이 의존성이다. Perl은 고정한 upstream slim base에서 상속되지만
저장소의 runtime 코드·Docker entrypoint·운영 스크립트에는 Perl script,
interpreter, `pack_ip`, Storable 실행 경로가 없다. 2026-08-05 재검토에서도
고정된 `python:3.11-slim` OCI digest가 upstream 최신 digest와 일치했고 Debian
trixie는 해당 glibc·Perl·Mbed TLS 패키지를 계속 vulnerable 또는 `no-dsa`로
표시했다. 따라서 unstable 패키지를 운영 이미지에 혼합하지 않고 정확한 현재
버전에 대한 한시 승인만 2026-08-19까지 갱신한다. 이 판단은 위험을 삭제하지
않으며 다음 연장은 다시 vendor 상태와 실제 실행 경로를 검토한 PR이 필요하다.

사용하지 않는 `postgresql-client` CLI는 런타임 공격 표면과 이미지 크기를 줄이기
위해 제거했지만 ECR 재검증 결과 Perl source finding의 원인은 아니었다. vendor
수정 base digest가 제공되면 acceptance를 삭제하고 전체 6-image scan과 release
연속성 게이트를 다시 실행한다.

집중 검증:

```powershell
python -m pytest tests/test_ecr_critical_scan_gate.py -q
python -m pytest tests/test_release_performance_contract.py -q
pwsh scripts/v1/test-workflow-governance-contract.ps1
```
