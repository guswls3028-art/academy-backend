# 알림톡 범용 템플릿 추가 등록 TODO

## 완료 (V1.1.1)
- [x] 클리닉 일정 안내 (KA01TP2604061058318608Hy40ZnTFZT) — 검수중
- [x] 클리닉 일정 변경 (KA01TP260406110706969XS06XRZveEk) — 검수중
- [x] 성적표발송 (KA01TP260406105458211774JKJ3OU55) — 검수중

## 추가 등록 필요
- [ ] **결제 안내** — payment_complete, payment_due_days_before 커버
  - 변수: 학원이름, 학생이름, 내용, 사이트링크
- [ ] **월간 리포트** — monthly_report_generated 커버
  - 변수: 학원이름, 학생이름, 내용, 사이트링크
- [ ] **퇴원 안내** — withdrawal_complete 전용 (현재는 기존 시스템 템플릿 사용)

## 제거 완료
- [x] ~~urgent_notice (긴급공지)~~ — 카카오 알림톡 정책 위반 (광고/긴급 공지 금지). 트리거에서 제거.

## 참고
- 각 템플릿은 솔라피 콘솔에서 수동 등록 후 검수 통과 필요
- 검수 통과 후 solapi_template_id를 AutoSendConfig에 연동
- 코드 매핑: `backend/apps/support/messaging/alimtalk_content_builders.py`
