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
- 변경 감지나 수동 전체 빌드가 공통 base 빌드를 선택하면 workflow run ID와
  attempt를 `APT_REFRESH_TOKEN`으로 전달한다. 이 값은 builder/runtime의 APT
  설치 레이어를 무효화하므로 오래된 `apt-get update` 결과를 BuildKit 캐시에서
  재사용하지 않고, 당시 Debian 저장소의 최신 보안 패키지를 설치한다. base가
  선택되지 않은 일반 앱 코드 빌드는 기존 digest를 재사용한다.
- pip Dependabot은 같은 호환 버전이 필요한 `boto3`/`botocore`를 한 PR로
  갱신하고, 개발 의존성 및 GitHub Actions minor/patch는 각각 묶어 중복 CI를
  줄인다. 모든 묶음은 개별 업데이트와 같은 전체 품질·이미지 scan 게이트를
  통과해야 한다.
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
  GUI가 없는 `opencv-python-headless`를 사용하므로 system `libglib2.0-0`을 OpenCV
  호환용으로 직접 설치하지 않는다. Video 후보에는 GLib가 남지 않으며, API·AI·Tools
  OCR 런타임은 Debian `tesseract-ocr`의 필수 전이 의존성으로만 정확한 GLib
  패키지를 포함한다. OpenCV import/FFmpeg smoke와 완료된 ECR scan이 이 경계를
  봉인한다.

## Critical 및 High 판정

1. 후보 manifest에 `source=built`인 각 digest의 scan 결과가 없으면 CI가
   repository-scoped `ecr:StartImageScan` 권한으로 scan을 호출한다. 재사용
   digest라는 이유로 scan을 건너뛰지 않는다. ECR이 동일 digest scan quota가
   이미 소비됐다고 응답해도 기존 scan의 `COMPLETE` readback은 끝까지 요구한다.
2. scan이 `COMPLETE`가 아니거나 Critical/High finding identity(CVE, package,
   version)가 불완전하면 실패 폐쇄한다. ECR severity count와 중복 제거한 exact
   identity 수가 다를 때도 결과를 신뢰하지 않는다.
3. 승인되지 않은 Critical은 하나라도 있으면 development/preprod/production으로
   진행하지 않는다.
4. 예외는 `docs/ssot/ecr-critical-risk-acceptance.json`에 repository, CVE,
   package, version, 만료일, Debian tracker와 도달 가능성 근거를 모두 정확히
   적은 항목만 허용한다. wildcard는 없으며 package version이나 CVE가 달라지면
   즉시 실패한다.
5. 만료일 다음 날부터는 scan 전에 전체 게이트가 실패한다. 만료 연장은 새
   vendor 상태와 실제 사용 경로를 다시 검토한 PR로만 가능하다.
6. High는 `docs/ssot/ecr-high-risk-baseline.json` schema 3의 repository별 상한과
   `acceptedHighFindings` exact identity를 모두 비교한다. metadata 없는 별도 known
   목록은 허용하지 않는다. 모든 High 항목은 exact Debian tracker, 실제 런타임의
   도달 가능성 근거와 hard expiration을 가져야 하며, 만료 다음 날에는 scan 전에
   실패한다. 수가 같아도 CVE, package, version 중 하나가 바뀌거나 다른 High가 기존
   항목을 대체하면 실패한다. 반대로 패키지 제거 또는 vendor 수정으로 기존 항목이
   사라져도 기준선이 stale하다고 실패하므로, 운영 scan readback을 근거로 identity와
   상한을 같은 PR에서 내려야 한다. 알 수 없는 항목, 누락된 기존 항목,
   identity/count 불일치 중 어느 것도 development/preprod로 진행할 수 없다.

2026-08-20 후보 `sha-31d3845d9...-run-32316780655-1`의 완료된 ECR scan을
재검토했다. Base·Video·Messaging은 glibc 1건과 Perl 3건으로 Critical 4건,
API·AI·Tools는 여기에 GLib 1건이 더해져 Critical 5건이었다. Debian 공식
tracker는 이 exact Trixie 패키지를 계속 affected 또는 `no-dsa`/minor로 표시하고
stable 수정 패키지를 제공하지 않는다. Academy의 Python entrypoint, Perl 미사용,
GLib D-Bus introspection 미사용 경계를 다시 확인한 뒤 이 다섯 exact
CVE/package/version 항목만 2026-09-19까지 재승인했다. 이전 Mbed TLS Critical
3건은 같은 후보의 완료 scan에서 더 이상 관측되지 않아 acceptance에서 즉시
삭제했다. 다시 나타나거나 identity가 달라지면 새 Critical로 실패 폐쇄한다.

2026-08-09 ECR 데이터베이스 갱신으로 동일한 GLib `2.84.4-3~deb13u3`에
`CVE-2026-58010`부터 `CVE-2026-58015`까지 여섯 High가 새로 나타났다. 후보와 직전
운영 digest의 finding identity를 대조해 패키지 변경이 아니라 신규 공개분임을
확인했다. Debian은 여섯 건 모두 trixie `no-dsa`/minor로 분류하며, 각각 GVariant
비정상 역직렬화, 잘못 생성된 GDateTime, `G_REGEX_RAW` case escape, 다중 문자
GIOChannel terminator, 빈 locale key-file 값, 악성 D-Bus 서버가 전제인 제한된
over-read/DoS 경로다. Academy OCR 경로는 이 API와 D-Bus를 사용하지 않는다.
따라서 Tesseract를 포함하는 API·AI·Tools만 검증된 현재 수치 18로 올렸고,
Base·Video·Messaging 상한 8은 유지했다. vendor 수정 패키지가 나오면 세 상한을
실제 scan readback에 맞춰 즉시 다시 낮춘다.

2026-08-11 ECR 데이터베이스 갱신은 같은 API·AI·Tools 이미지의 Debian
`libssh2` `1.11.1-1+deb13u1`에 `CVE-2026-58050`과 `CVE-2026-58051`을
추가했다. 직전 운영 AI digest와 새 후보의 finding identity를 비교했을 때
추가분은 이 두 건뿐이었고, 새 후보 여섯 개의 완료된 scan은 Base·Video·Messaging
High 8, API·AI·Tools High 20을 각각 반환했다. Debian tracker에는 아직 stable
수정 버전이 없다. 58050은 악성 SSH publickey 응답을 처리하는 32-bit allocation
overflow이고 Academy 운영 이미지는 ARM64다. 58051도 악성 SSH 서버의 publickey
subsystem 응답과 오류 cleanup이 전제다. 저장소의 앱·워커 entrypoint에는 SSH,
SFTP, SCP, Paramiko 또는 libssh2 실행 경로가 없고 운영 원격 명령은 컨테이너 밖의
AWS SSM이 소유한다. 따라서 불안정 Debian 패키지를 혼합하지 않고 완료된 scan의
현재 수치 20으로 세 상한만 갱신한다. vendor 추적은
`https://security-tracker.debian.org/tracker/CVE-2026-58050`과
`https://security-tracker.debian.org/tracker/CVE-2026-58051`이며, 수정 패키지가
나오면 새 이미지를 빌드·스캔하고 상한을 즉시 낮춘다.

2026-08-15 현재 같은 `libssh2` 패키지에 `CVE-2026-66032`부터
`CVE-2026-66035`까지 네 High가 추가되어 API·AI·Tools의 exact 집합은 공통 8건,
GLib 6건, libssh2 6건인 총 20건이다. Debian tracker는 trixie
`1.11.1-1+deb13u1`을 네 건 모두 vulnerable로, forky/sid `1.11.1-5`를 fixed로
표시한다. Academy 이미지에 다른 suite 패키지를 섞지 않으며, trixie 수정 패키지가
나오면 새 digest의 완료 scan에서 제거를 확인한 뒤 identity와 상한을 함께 낮춘다.
현재 앱·워커 entrypoint에는 SSH/SFTP client 호출 경로가 없다는 도달 가능성 경계는
유지하지만, 그 사실이 다른 CVE나 버전으로의 조용한 교체를 허용하지는 않는다.

2026-08-22 후보 `sha-8f8014d5a...-run-32493438087-1`의 완료된 ECR scan에서
새 API digest `sha256:1c9210dc...`와 AI digest `sha256:b395f75c...`는 동일한
`libssh2` `1.11.1-1+deb13u1`을 유지하면서 `CVE-2026-66032`만 남기고
`CVE-2026-58050`, `CVE-2026-58051`, `CVE-2026-66033`, `CVE-2026-66034`,
`CVE-2026-66035`를 더 이상 반환하지 않았다. 두 digest의 exact High 집합은 공통
8건, GLib 6건, libssh2 1건인 총 15건이므로 API·AI 상한과 다섯 finding의
repository identity를 함께 낮췄다. 같은 후보의 Video·Messaging은 기존 8건을
유지했다. 후속 후보 `sha-d06e895c1...-run-32498688185-1`은 새 Tools digest
`sha256:35123a457b7903688bd7553f5fb84a6938be5f72aef4f60d2f105618ed6b7481`을
빌드했다. 그 digest의 완료 scan도 동일한 다섯 CVE를 더 이상 반환하지 않고 공통
8건, GLib 6건, libssh2 1건인 exact High 15건을 반환했다. 따라서 Tools 상한을
15로 낮추고 기준선에서 다섯 finding을 제거했다. 이는 패키지 업그레이드나 위험
승인 확대가 아니라 ECR의 digest별 완료 scan readback을 exact 기준선에 반영한
것이다.

후속 후보 `sha-f92c02728...-run-32532674189-1`의 완료된 ECR scan에서는 API
`sha256:dd4ee4be...`, AI `sha256:ee976e64...`, Tools `sha256:bb19687d...` 세
digest 모두 같은 패키지의 마지막 `CVE-2026-66032`도 더 이상 반환하지 않았다.
세 digest의 exact High 집합은 공통 8건과 GLib 6건인 14건이며 나머지 finding
identity와 package version은 변하지 않았다. 따라서 세 저장소 상한을 14로
낮추고 마지막 libssh2 identity를 제거한다. 해당 후보는 stale 기준선 때문에
development 진입 전에 실패했고 production을 변경하지 않았으며, 이 축소를 포함한
다음 후보가 전체 release gate를 다시 통과해야 한다.

2026-08-23 sender/runtime 후보 `sha-43e9946e...-run-32614790812-1`의 새 API
`sha256:ebf04e84...`와 AI `sha256:d5cf49af...` 완료 scan은 같은 Debian trixie
`libssh2` `1.11.1-1+deb13u1`에서 앞서 사라졌던 여섯 High를 모두 다시 반환했다.
두 digest 모두 공통 8건, GLib 6건, libssh2 6건인 exact 20건이며 package version은
변하지 않았다. Debian tracker에서 trixie는 여섯 건 모두 vulnerable이고 stable
수정 패키지가 없다. 저장소와 두 runtime entrypoint에
SSH, SFTP, SCP, Paramiko 또는 libssh2 client 경로가 없고, 이 패키지는 API·AI의
Tesseract/libcurl 전이 의존으로만 존재한다. 따라서 다른 Debian suite 패키지를
혼합하지 않고 여섯 exact identity만 `acceptedHighFindings`에서 2026-09-19까지
한시 수용한다. 운영 Tools digest의 11:05 KST 완료 scan은 여전히 14건이고 동일 digest
재scan은 ECR quota로 거부됐으므로 Tools를 추론으로 승인하지 않고 상한 14를 유지한다.
새 Tools digest가 같은 finding을 실제 반환하면 그 exact 후보에서 별도 검토한다.
Debian stable fix나 API·AI ECR identity 변화가 먼저 나오면 acceptance를 즉시 제거한다.
run `32614790812`는 이 판정 전에 실패하여 development/preprod/production runtime을
변경하지 않았고 shared lock을 반환했다.

현재 Critical 한시 항목은 Debian stable에 수정본이 아직 없거나 Debian이
`no-dsa`/minor로 분류한 glibc·GLib·Perl finding이다. GLib의
`g_dbus_node_info_new_for_xml` malformed introspection-XML 경로는 OCR CLI와
Academy Python 워커가 호출하지 않으며, 워커는 D-Bus introspection XML을 입력으로
받지 않는다. glibc 취약 native `scanf` 경로도 Academy Python 앱의 실행
경로가 아니다. Perl은 고정한 upstream slim base에서 상속되지만
저장소의 runtime 코드·Docker entrypoint·운영 스크립트에는 Perl script,
interpreter, `pack_ip`, Storable 실행 경로가 없다. Debian trixie는 해당
glibc·Perl·GLib 패키지를 계속 vulnerable 또는 `no-dsa`로 표시한다. 따라서
unstable 패키지를 운영 이미지에 혼합하지 않고 정확한 현재 버전에 대한 한시
승인만 2026-09-19까지 유지한다. 이 판단은 위험을 삭제하지 않으며 다음 연장은
다시 vendor 상태와 실제 실행 경로를 검토한 PR이 필요하다.

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
