# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

---

## 확정 RCA (한 문장)

**sg-app의 8000 포트 인바운드가 10.0.0.0/16으로만 설정되어 있어, VPC CIDR 172.30.0.0/16인 ALB가 EC2:8000 헬스체크에 도달하지 못해 Target.Timeout이 발생하였고, 동시에 API 인스턴스에서 academy-api 컨테이너가 기동되지 않아 8000 포트가 열려 있지 않음.**  
조치로 SG에 172.30.0.0/16(SSOT VpcCidr) 8000 규칙을 추가한 후, Target 상태는 Timeout → FailedHealthChecks로 변경됨(연결 도달·응답 실패). 신규 인스턴스에서도 컨테이너 미기동이 확인되어, 게이트 A 미달 잔여 원인은 **앱 이미지/ENV/시작 로직**으로 귀결됨.

---

## 수정한 파일 / SSOT 키

| 파일 | 변경 내용 | SSOT 키 |
|------|-----------|---------|
| resources/network.ps1 | sg-app 8000 인바운드: 신규 생성 시 `VpcCidr` 사용, 기존 SG에 8000 from VpcCidr 없으면 추가 | network.vpcCidr |
| resources/api.ps1 | UserData 내 docker run 실패 시 `/var/log/academy-api-userdata.log` 기록 | api.* (기존) |

(이전 PHASE A에서 적용된 SQS/bootstrap/API LT 주석 제거/Ops CE 재시도 등은 생략.)

---

## 배포/검증 명령 (실행 순서)

1. **배포**
   ```powershell
   pwsh -File scripts/v1/run-with-env.ps1 -- pwsh -File scripts/v1/deploy.ps1 -Env prod
   ```
   (필요 시 `-SkipBuild -SkipNetprobe -RelaxedValidation` 추가)

2. **검증**
   ```powershell
   pwsh -File scripts/v1/run-with-env.ps1 -- pwsh -File scripts/v1/run-deploy-verification.ps1 -AwsProfile default
   ```

---

## 최종 게이트 A/B 결과

| 게이트 | 조건 | 결과 |
|--------|------|------|
| **GATE-A** | /health 200, target healthy ≥1, API LT NoOp, FAIL 0 | **미달** — /health unreachable, target healthy 0/3. 원인: academy-api 컨테이너 미기동. |
| **GATE-B** | 인스턴스 수 desired(2) 수렴, 레거시 정리, NO-GO 아님 | **보류** — GATE-A 통과 후 수행. |

---

## 남은 WARNING / 영향·완화

- **Drift:** API LT/academy-v1-api-lt — Action NewVersion. 배포 시 SSOT 기준 LT 버전 7 생성·반영됨. 다음 검증 주기에서 drift 수렴 가능.
- **앱 미기동:** academy-api 컨테이너가 EC2에서 기동하지 않음. 인스턴스 내 `/var/log/cloud-init-output.log`, `/var/log/academy-api-userdata.log` 확인 및 이미지·ENV·DB 연결·gunicorn 바인딩(0.0.0.0:8000) 점검 필요.

---

## 최종 GO/NO-GO 판정

| 판정 | 내용 |
|------|------|
| **NO-GO** | GATE-A 미달. /health 200 및 target healthy ≥1 확보 후 재검증 필요. |

- **FAIL 1건 이상** → NO-GO.
- **WARNING만** → CONDITIONAL GO.
- **PASS만** → GO.

---

## 연관 보고서

- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트·SQS·Video·관측·GO/NO-GO 상세
- [rca.latest.md](./rca.latest.md) — 근본원인 분석·Target health·SSM 결과·SG/TG 비교
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift
