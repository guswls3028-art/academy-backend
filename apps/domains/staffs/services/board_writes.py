import hashlib
import json
from uuid import UUID

from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.domains.staffs.models import StaffBoardWriteReceipt


class BoardWriteConflict(APIException):
    status_code = 409
    default_detail = "같은 요청 키로 다른 내용을 저장할 수 없습니다."


class BoardWrite:
    """Reserve one logical write within the transaction that creates its result."""

    def __init__(self, request, target, fields, upload=None):
        self.tenant_id = request.tenant.pk
        self.scope_key = None
        key = request.headers.get("Idempotency-Key")
        if key is None:
            return
        try:
            operation = UUID(key)
        except ValueError:
            raise serializers.ValidationError({"Idempotency-Key": "UUID 형식의 요청 키가 필요합니다."})
        scope = f"{self.tenant_id}:{request.user.pk}:{target}:{operation}"
        self.scope_key = hashlib.sha256(scope.encode()).hexdigest()
        payload = dict(fields)
        if upload is not None:
            digest = hashlib.sha256()
            for chunk in upload.chunks():
                digest.update(chunk)
            upload.seek(0)
            payload["file"] = [upload.name, digest.hexdigest()]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.payload_hash = hashlib.sha256(encoded.encode()).hexdigest()

    def existing_result(self):
        if not self.scope_key:
            return None
        receipt = StaffBoardWriteReceipt.objects.filter(tenant_id=self.tenant_id, scope_key=self.scope_key).first()
        return self.result(receipt)

    def reserve(self):
        if not self.scope_key:
            return None
        receipt, _ = StaffBoardWriteReceipt.objects.get_or_create(
            tenant_id=self.tenant_id, scope_key=self.scope_key, defaults={"payload_hash": self.payload_hash},
        )
        return receipt

    def result(self, receipt):
        if receipt is not None:
            if receipt.payload_hash != self.payload_hash:
                raise BoardWriteConflict()
            return receipt.result_id
        return None

    @staticmethod
    def finish(receipt, result):
        if receipt is not None:
            receipt.result_id = result.pk
            receipt.save(update_fields=["result_id"])
