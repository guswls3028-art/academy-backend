#!/usr/bin/env python
"""모든 미검수 템플릿을 솔라피에 일괄 검수 신청."""
import os
import sys
import time
import django

sys.stdout.reconfigure(encoding="utf-8")
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(base_dir, "apps", "api"))
sys.path.insert(0, base_dir)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.conf import settings  # noqa: E402
from apps.support.messaging.models import MessageTemplate  # noqa: E402
from apps.support.messaging.solapi_template_client import (  # noqa: E402
    create_kakao_template,
    validate_template_variables,
)

TENANT_ID = 1
PFID = os.environ.get("SOLAPI_KAKAO_PF_ID", "").strip() or getattr(settings, "SOLAPI_KAKAO_PF_ID", "")
API_KEY = os.environ.get("SOLAPI_API_KEY", "").strip() or getattr(settings, "SOLAPI_API_KEY", "")
API_SECRET = os.environ.get("SOLAPI_API_SECRET", "").strip() or getattr(settings, "SOLAPI_API_SECRET", "")

if not PFID:
    print("ERROR: SOLAPI_KAKAO_PF_ID 환경변수 필요")
    sys.exit(1)
if not API_KEY or not API_SECRET:
    print("ERROR: SOLAPI_API_KEY / SOLAPI_API_SECRET 환경변수 필요")
    sys.exit(1)

print(f"PFID: {PFID}")
print(f"API_KEY: {API_KEY[:8]}...")
print()

# 미검수 템플릿 (solapi_template_id 없거나 solapi_status != APPROVED)
templates = list(
    MessageTemplate.objects.filter(
        tenant_id=TENANT_ID,
    ).exclude(
        solapi_status="APPROVED",
    ).order_by("id")
)

print(f"검수 신청 대상: {len(templates)}개\n")

success = 0
failed = 0

for t in templates:
    # 본문: subject + body
    content = (t.subject.strip() + "\n" + t.body).strip() if t.subject else t.body
    content = content.strip()

    # 변수 검증
    ok, errs = validate_template_variables(content)
    if not ok:
        print(f"  SKIP | {t.name} — 변수 오류: {'; '.join(errs)}")
        failed += 1
        continue

    # 이미 등록된 건 스킵
    if t.solapi_template_id and t.solapi_status == "PENDING":
        print(f"  SKIP | {t.name} — 이미 PENDING (ID: {t.solapi_template_id})")
        continue

    try:
        result = create_kakao_template(
            api_key=API_KEY,
            api_secret=API_SECRET,
            channel_id=PFID,
            name=t.name,
            content=content,
            category_code="TE",
        )
        template_id = result.get("templateId", "")
        t.solapi_template_id = template_id
        t.solapi_status = "PENDING"
        t.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])
        print(f"  OK   | {t.name} → {template_id}")
        success += 1
    except ValueError as e:
        print(f"  FAIL | {t.name} — {e}")
        failed += 1
    except Exception as e:
        print(f"  ERR  | {t.name} — {e}")
        failed += 1

    # API rate limit 방지 (1초 간격)
    time.sleep(1)

print(f"\n완료: 성공 {success}개, 실패 {failed}개")
print(f"전체 PENDING: {MessageTemplate.objects.filter(tenant_id=TENANT_ID, solapi_status='PENDING').count()}개")
