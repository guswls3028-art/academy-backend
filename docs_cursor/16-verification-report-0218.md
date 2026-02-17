# 검증 보고서 (2026-02-18)

**범위**: 0217 이후 메시징·워커·자동발송 관련 변경사항 문서화, 코드 대조, 정합성 검사, 빌드 검증.

---

## 1. 문서 반영 사항

### 1.1 신규 문서

| 문서 | 내용 |
|------|------|
| `docs_cursor/15-messaging-worker-and-message-flow.md` | Messaging Worker, message_mode, 자동발송, API 엔드포인트 SSOT |

### 1.2 수정 문서

| 문서 | 변경 내용 |
|------|----------|
| `docs_cursor/README.md` | 14, 15번 문서 목록에 추가 |
| `docs_cursor/14-solapi-check-guide.md` | Tenant.messaging_sender 우선순위 안내 추가 |
| `docs_cursor/03-settings-env.md` | Messaging Worker Solapi ENV 섹션 추가 |
| `docs_cursor/02-core-apis.md` | Messaging API 참조(15번 문서) 추가 |
| `apps/worker/messaging_worker/README.md` | 발신번호 우선순위(payload→Tenant→ENV) 섹션 추가 |

### 1.3 0216 관련

- `docs/0216/*` 등 0216 폴더/문서는 **변경 없음** (유지).

---

## 2. 코드-문서 대조 결과

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| message_mode | sms/alimtalk/both | sqs_main.py L323~352 | ✓ |
| 발신번호 우선순위 | payload→Tenant→SOLAPI_SENDER | sqs_main.py L276~283 | ✓ |
| API 경로 /messaging/* | 15번 문서 | urls.py | ✓ |
| AutoSendConfig 트리거 | student_signup, clinic_reminder 등 | models.py | ✓ |
| SQS 메시지 형식 | message_mode, template_id 등 | sqs_queue.py | ✓ |

---

## 3. 백엔드-프론트엔드 API 정합성

| API | Method | Backend Path | Frontend PREFIX | 일치 |
|-----|--------|--------------|-----------------|------|
| Messaging Info | GET/PATCH | /api/v1/messaging/info/ | /messaging/info/ | ✓ |
| Verify Sender | POST | /api/v1/messaging/verify-sender/ | /messaging/verify-sender/ | ✓ |
| Charge | POST | /api/v1/messaging/charge/ | /messaging/charge/ | ✓ |
| Log | GET | /api/v1/messaging/log/ | /messaging/log/ | ✓ |
| Send | POST | /api/v1/messaging/send/ | /messaging/send/ | ✓ |
| Templates | GET/POST | /api/v1/messaging/templates/ | /messaging/templates/ | ✓ |
| Template Detail | GET/PATCH/DELETE | /api/v1/messaging/templates/\<pk\>/ | /messaging/templates/\<id\>/ | ✓ |
| Submit Review | POST | .../submit-review/ | .../submit-review/ | ✓ |
| Auto-Send | GET/PATCH | /api/v1/messaging/auto-send/ | /messaging/auto-send/ | ✓ |

**Payload 정합성**:
- `sendMessage`: student_ids, send_to, message_mode, template_id, raw_body, raw_subject ✓
- `updateAutoSendConfigs`: configs: [{ trigger, template_id, enabled, message_mode }] ✓

---

## 4. 빌드 결과

| 대상 | 명령 | 결과 |
|------|------|------|
| Backend | `python manage.py check` | ✓ 통과 |
| Frontend | `npm run build` | ✓ 통과 |

**참고**: DecimalField min_value 경고는 `Decimal("1")`로 수정하여 해소.

---

## 5. 적용된 최적화

- `ChargeRequestSerializer`: `min_value=1` → `min_value=Decimal("1")` (DRF 경고 제거)

---

## 6. 관련 파일 요약

### Backend (academy)
- `apps/support/messaging/` (models, views, serializers, urls, services, sqs_queue, selectors)
- `apps/worker/messaging_worker/` (sqs_main, config)
- `apps/core/models/tenant.py` (messaging_sender)

### Frontend (academyfront)
- `src/features/messages/` (api, hooks, pages, layout, components)
- 라우트: templates, send, auto-send, log, settings

---

## 7. 체크리스트 (배포 전)

- [ ] Messaging Worker ENV (SOLAPI_*, MESSAGING_SQS_QUEUE_NAME) 설정
- [ ] SQS `academy-messaging-jobs` 큐 생성
- [ ] 마이그레이션 `messaging.0002_autosendconfig` 적용
- [ ] 발신번호 솔라피 등록·인증 확인
