"""Persist non-delivering Alimtalk previews for the isolated Dawon admin room."""

from hashlib import sha256

from django.db import transaction
from django.utils.crypto import salted_hmac
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from apps.domains.messaging.models import NotificationLog
from apps.domains.students.models import Student

from .dawon_staff import StaffRequest


ORIGIN = "dawon_demo_preview"
MODE = "demo"
TEMPLATES = {
    "notice": ("학원 공지", "다원수학 학원 공지가 등록되었습니다. 학습 앱에서 내용을 확인해 주세요."),
    "class": ("수업 안내", "다원수학 수업 안내가 등록되었습니다. 학습 앱에서 수업 일정을 확인해 주세요."),
    "clinic": ("클리닉 안내", "다원수학 클리닉 안내가 등록되었습니다. 학습 앱에서 장소와 시간을 확인해 주세요."),
}


class PreviewInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    template_id = serializers.ChoiceField(choices=tuple(TEMPLATES))
    audience = serializers.ChoiceField(choices=("student", "parent"), default="parent")
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=2**63-1), allow_empty=False, max_length=100,
    )


def receipt(record):
    return {"id": record.pk, "batch_id": str(record.batch_id), "template_id": record.origin_id,
            "template_name": record.template_summary, "body": record.message_body,
            "audience": record.target_type or "parent", "recipient_count": int(record.recipient_summary), "created_at": record.sent_at, "simulated": True}


class DawonMessagePreviewView(StaffRequest):
    parser_classes = [JSONParser]

    @extend_schema(exclude=True)
    def get(self, request):
        records = NotificationLog.objects.filter(tenant=request.tenant, origin_type=ORIGIN, message_mode=MODE)
        return Response({"templates": [{"id": key, "name": name, "body": body} for key, (name, body) in TEMPLATES.items()],
                         "receipts": [receipt(record) for record in records.order_by("-id")[:50]]})

    @extend_schema(exclude=True)
    @transaction.atomic
    def post(self, request):
        payload = PreviewInput(data=request.data)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data
        template = values["template_id"]
        audience = values["audience"]
        ids = sorted(set(values["student_ids"]))
        batch = values["request_id"]
        key = sha256(f"{ORIGIN}:{batch}".encode()).hexdigest()
        # Persist only a keyed recipient-set fingerprint and count. No phone,
        # plain recipient identity, job, provider, or billing event is created.
        fingerprint = salted_hmac(ORIGIN, f"{request.tenant.id}:{audience}:{template}:{ids}", algorithm="sha256").hexdigest()
        previous = NotificationLog.objects.filter(
            tenant=request.tenant, message_mode=MODE, business_idempotency_key=key, origin_type=ORIGIN,
        ).first()
        if previous is not None:
            if previous.recipient_fingerprint != fingerprint:
                raise ValidationError("이미 사용한 발송 체험 요청입니다.")
            return Response(receipt(previous))
        if Student.objects.filter(tenant=request.tenant, deleted_at__isnull=True, pk__in=ids).count() != len(ids):
            raise ValidationError("조회할 수 없는 학생이 포함됐습니다.")
        name, body = TEMPLATES[template]
        record, created = NotificationLog.objects.get_or_create(
            tenant=request.tenant, message_mode=MODE, business_idempotency_key=key,
            defaults={"origin_type": ORIGIN, "origin_id": template, "batch_id": batch,
                      "recipient_fingerprint": fingerprint, "recipient_summary": str(len(ids)),
                      "target_type": audience, "template_summary": name, "message_body": body, "status": "simulated"},
        )
        if record.origin_type != ORIGIN or record.recipient_fingerprint != fingerprint:
            raise ValidationError("이미 사용한 발송 체험 요청입니다.")
        return Response(receipt(record), status=201 if created else 200)
