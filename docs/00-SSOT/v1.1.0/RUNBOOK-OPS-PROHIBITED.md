# 운영 금지 규칙

**Version:** V1.1.0 | **최종 수정:** 2026-03-15

> 아래 항목은 **정상모드·비상모드 모두에서 절대 금지**다. 위반 시 서비스 장애, 데이터 유실, 보안 사고가 발생한다.
> 비상모드 정의: `RUNBOOK-EMERGENCY-MODE.md` 참조.

---

## 1. CI에서 인프라 프로비저닝 금지

`deploy.ps1`은 **로컬 또는 SSH에서만** 실행한다. GitHub Actions에서 ASG/ALB/RDS 등 인프라를 생성/삭제하는 코드를 넣지 않는다.

CI는 **이미지 빌드 → 푸시 → ASG refresh**만 한다. 인프라 변경은 수동.

---

## 2. 멀티테넌트 혼합 금지

- 테넌트 간 데이터 접근, 폴백, 기본값 공유 **절대 금지**
- `default_tenant`, `fallback_tenant`, 테넌트 없는 쿼리 **금지**
- 모든 데이터 쿼리에 테넌트 필터 필수

**위반 = 보안 사고.**

---

## 3. 워커 ASG min < 1 금지

API, Messaging, AI 워커의 ASG `MinSize`를 0으로 설정하지 않는다.

```
academy-v1-api-asg              → min >= 1
academy-v1-messaging-worker-asg → min >= 1
academy-v1-ai-worker-asg        → min >= 1
```

min=0은 메시지 유실, 서비스 중단을 초래한다.

---

## 4. 장애 시 Messaging fail-open 전환 금지

메시징 장애가 발생해도 **인증 없이 메시지를 보내는 모드로 전환하지 않는다.**

실패한 메시지는 DLQ에 보관하고, 원인 해결 후 재처리한다.

---

## 5. 운영 중 DB column drop/rename 금지

운영 중인 DB에서 컬럼을 삭제하거나 이름을 바꾸면 **즉시 서비스 장애**가 발생한다.

**반드시 2-release 프로세스:**
1. Release N: 새 컬럼 추가 (nullable/default). 기존 컬럼 유지.
2. Release N+1: 기존 컬럼 삭제 (이전 코드가 프로덕션에서 완전히 제거된 후).

단일 릴리스에서 drop/rename + 코드 변경을 동시에 하면 **무중단 배포가 깨진다.**

---

## 6. ECR lifecycle policy 삭제 후 미적용 금지

ECR 리포지토리의 lifecycle policy를 삭제하거나 새 리포지토리를 만들 때 **반드시 lifecycle policy를 적용**한다.

미적용 시 이미지가 무한 누적되어 **비용이 지속 증가**한다.

적용 기준: sha- 태그 10개 유지, untagged 1일 후 삭제.

---

## 7. git push --force to main 금지

`main` 브랜치에 `--force` push를 하지 않는다.

- CI/CD 히스토리가 깨진다
- SHA 기반 롤백이 불가능해진다
- 다른 커밋이 유실된다

---

## 8. cancel-in-progress: true 전환 금지

GitHub Actions 워크플로우의 `concurrency.cancel-in-progress`를 `true`로 설정하지 않는다.

진행 중인 배포가 취소되면:
- ASG refresh가 중간에 중단 → 인스턴스 불일치
- Migration이 중간에 중단 → DB 상태 불일치
- 서비스 장애

현재 설정은 `cancel-in-progress: false`이며, 이전 배포 완료를 기다린 후 다음 배포가 실행된다.
