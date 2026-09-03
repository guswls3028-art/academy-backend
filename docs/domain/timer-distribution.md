# 타이머 Windows 배포와 신뢰 경계

## 현재 사용자 흐름

관리자와 강사는 별도 실행 파일 없이 학원 도메인의 타이머를 사용한다.

- 관리자: `/workspace/tools/stopwatch`
- 강사: `/workspace/mobile/tools/stopwatch`
- Windows에서는 Edge 또는 Chrome의 앱 설치 기능으로 해당 화면을 PWA로
  설치할 수 있다. 설치 후에도 실행 주체와 업데이트 출처는 사용자가 확인한
  학원 HTTPS 도메인이다.
- 타이머 화면을 한 번 정상 로드한 뒤에는 service worker가 앱 셸과 정적
  자산을 보존한다. API 응답은 캐시하지 않는다.

프런트엔드 상호작용과 오프라인 셸 계약은
[Academy frontend TEACHER-TOOLS.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/TEACHER-TOOLS.md)가
소유한다.

## 중단된 레거시 배포

과거 `GET /api/v1/tools/timer/download/`는 R2의 PyInstaller ZIP에 대한
presigned URL을 반환했다. 그 ZIP의 실행 파일에는 Authenticode 서명,
publisher, 제품 버전이 없었고 화면과 README는 SmartScreen 경고에서
`추가 정보`와 `실행`을 누르도록 안내했다. ZIP 포장은 실행 파일의 신뢰를
높이지 않으며 Smart App Control이 켜진 Windows 11의 실행 정책을 충족하지
못한다.

이 배포는 다음과 같이 철회한다.

- API는 인증과 tenant/staff 권한을 그대로 검사한 뒤 항상 HTTP 410과
  `trusted_timer_distribution_required`를 반환한다.
- API는 R2 URL을 생성하지 않는다. 기존 R2 객체는 사용자 데이터가 아니지만
  조사와 롤백 증거를 위해 보존하고 제품에서는 연결하지 않는다.
- `timer_tenants.json`과 저장소 밖 빌드 스크립트 연결은 제거했다. Git 밖의
  PyInstaller 소스를 정식 빌드 소스로 간주하지 않는다.
- `English_Timer.exe`, `English_Timer (1).exe`, `Timer.exe` 같은 기존 파일은
  새 버전이 아니다. 사용자는 실행하거나 Smart App Control을 끄지 말고
  삭제한 뒤 웹/PWA 타이머를 사용한다.

기존 데이터나 타이머 기록의 migration은 없다. 레거시 앱은 로컬에서만
동작했고 서버 canonical 데이터를 쓰지 않았다.

## Windows 실행 파일을 다시 열기 위한 필수 게이트

직접 다운로드를 복구하려면 한 release가 아래 조건을 모두 만족해야 한다.

1. 소스, lockfile, 재현 가능한 Windows 빌드, 패키징 스크립트를 Git에서
   소유한다.
2. Microsoft Store가 다시 서명한 MSIX를 배포하거나, Microsoft Artifact
   Signing 또는 Microsoft Trusted Root Program CA의 코드서명 인증서로 모든
   EXE/MSI/MSIX를 Authenticode 서명한다. self-signed 인증서는 운영 배포에
   사용할 수 없다.
3. SHA-256 파일 digest와 RFC 3161 timestamp를 포함하고 `signtool verify /pa
   /all /v`와 `Get-AuthenticodeSignature`가 유효한 동일 publisher를 반환한다.
4. 불변 manifest에 `publisher`, `product_name`, `version`, `sha256`, `size`,
   `timestamp`, `r2_key`를 봉인한다. API는 R2 HEAD의 크기와 SHA-256 metadata가
   manifest와 모두 일치할 때만 presigned URL을 발급한다.
5. 다운로드 이름은 제품명과 버전을 포함한 단일 정식 이름을 사용하고, 화면에
   publisher·version·SHA-256을 함께 보여 오래된 `(1)` 중복본과 구분한다.
6. Smart App Control `On (Enforcement)`인 깨끗한 Windows 11 장치에서 설치,
   시작, 타이머 설정·시작·정지·초기화, 스톱워치·랩, 프로젝터·전체화면,
   재실행과 제거를 검증한다. 보안 기능을 끄는 절차는 허용하지 않는다.
7. Defender 최신 서명 검사와 Store/서명 서비스의 악성코드·정책 검사를
   통과하고, exact artifact hash를 release 증거에 남긴다.

Microsoft Store MSIX는 Store 인증 뒤 Microsoft 인증서로 다시 서명되므로
현재 계정·인증서가 없는 경우의 기본 선택이다. Partner Center 앱 identity와
publisher가 발급되기 전에는 placeholder identity로 운영 패키지를 만들거나
unsigned ZIP 링크를 임시 복구하지 않는다.

## 구현과 집중 검증

- endpoint: `apps/domains/tools/timer_download_view.py`
- route: `apps/domains/tools/urls.py`
- backend regression: `tests/test_timer_distribution_safety.py`
- frontend regression: `e2e/admin/stopwatch-visual-runtime.mock.spec.ts`

```powershell
python -m pytest tests/test_timer_distribution_safety.py -q
```
