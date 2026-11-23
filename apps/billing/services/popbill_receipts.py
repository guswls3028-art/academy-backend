"""Durable Popbill issue requests, payment-scoped recipients and provider reconciliation."""

import json
import os
import re
import secrets
import subprocess
import sys
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing.models import PopbillReceipt, TaxInvoiceIssue


def _keyring():
    return MultiFernet(
        [Fernet(key.strip()) for key in settings.BILLING_POPBILL_RECIPIENT_KEYS.split(",") if key.strip()]
    )


def enabled():
    mode = settings.BILLING_POPBILL_MODE
    if mode not in ("test", "live"):
        return False
    # A production rollout may only record real documents. Isolated deployments may only test.
    production = settings.SETTINGS_MODULE == "apps.api.config.settings.prod"
    if os.getenv("ACADEMY_RUNTIME_ENV", "").strip().lower() == "preprod":
        return False
    if (mode == "live") != production:
        return False
    try:
        _keyring()
    except (ValueError, TypeError):
        return False
    return bool(
        re.fullmatch(r"[0-9]{10}", settings.BILLING_POPBILL_CORP_NUM)
        and settings.BILLING_POPBILL_LINK_ID
        and settings.BILLING_POPBILL_SECRET_KEY
        and settings.BILLING_POPBILL_CORP_NAME
        and settings.BILLING_POPBILL_CEO_NAME
    )


def recipient_data(identifier="", name="", email=""):
    return {"identifier": identifier, "name": name, "email": email}


def _recipient(row):
    if not row.recipient_ciphertext:
        return {}
    data = json.loads(_keyring().decrypt(row.recipient_ciphertext.encode()))
    if data["key"] != row.document_key:
        raise ValueError("Receipt binding mismatch")
    return data["recipient"]


def matches(row, kind, recipient):
    return row.kind == kind and (not row.recipient_ciphertext or _recipient(row) == recipient)


def prepare(issue, kind, recipient):
    key = secrets.token_hex(12)
    ciphertext = ""
    if kind != "BUSINESS_TAX":
        data = json.dumps({"key": key, "recipient": recipient}).encode()
        ciphertext = _keyring().encrypt(data).decode()
    return PopbillReceipt.objects.create(
        issue=issue,
        kind=kind,
        document_key=key,
        recipient_ciphertext=ciphertext,
        issuer=settings.BILLING_POPBILL_CORP_NUM,
        environment=settings.BILLING_POPBILL_MODE,
    )


def _document(row):
    issue = row.issue
    invoice = issue.invoice
    paid = timezone.localtime(invoice.paid_at)
    day = paid.strftime("%Y%m%d")
    amounts = dict(totalAmount=str(invoice.total_amount))
    recipient = _recipient(row)
    if row.kind == "CASH":
        return dict(
            **amounts,
            mgtKey=row.document_key,
            tradeType="승인거래",
            tradeUsage="소득공제용",
            tradeOpt="일반",
            taxationType="과세",
            supplyCost=str(invoice.supply_amount),
            tax=str(invoice.tax_amount),
            serviceFee="0",
            tradeDT=paid.strftime("%Y%m%d%H%M%S"),
            identityNum=recipient["identifier"],
            itemName="프로그램 구독료",
            franchiseCorpNum=row.issuer,
            franchiseCorpName=settings.BILLING_POPBILL_CORP_NAME,
            franchiseCEOName=settings.BILLING_POPBILL_CEO_NAME,
        )
    profile = issue.business_profile_snapshot
    personal = row.kind == "PERSONAL_TAX"
    return dict(
        **amounts,
        writeDate=day,
        chargeDirection="정과금",
        issueType="정발행",
        purposeType="영수",
        taxType="과세",
        supplyCostTotal=str(invoice.supply_amount),
        taxTotal=str(invoice.tax_amount),
        invoicerMgtKey=row.document_key,
        invoicerCorpNum=row.issuer,
        invoicerCorpName=settings.BILLING_POPBILL_CORP_NAME,
        invoicerCEOName=settings.BILLING_POPBILL_CEO_NAME,
        invoiceeType="개인" if personal else "사업자",
        invoiceeCorpNum=recipient["identifier"] if personal else profile["business_registration_number"],
        invoiceeCorpName=recipient["name"] if personal else profile["business_name"],
        invoiceeCEOName=recipient["name"] if personal else profile["representative_name"],
        invoiceeEmail1=recipient["email"] if personal else profile["tax_invoice_email"],
        detailList=[
            dict(
                serialNum=1,
                purchaseDT=day,
                itemName="프로그램 구독료",
                supplyCost=str(invoice.supply_amount),
                tax=str(invoice.tax_amount),
            )
        ],
    )


def _exchange(row):
    document = None if row.provider_state else _document(row)
    payload = dict(
        document=document,
        kind=row.kind,
        key=row.document_key,
        corp=row.issuer,
        mode=row.environment,
        link_id=settings.BILLING_POPBILL_LINK_ID,
        secret_key=settings.BILLING_POPBILL_SECRET_KEY,
    )
    # Secrets travel on a private pipe. The SDK process never writes diagnostics to application logs.
    run = subprocess.run(
        [sys.executable, "-m", "apps.billing.popbill_gateway"],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=12,
        check=True,
    )
    return json.loads(run.stdout)


def dispatch(issue_id, *, retry=False):
    now = timezone.now()
    lease = uuid.uuid4()
    with transaction.atomic():
        row = PopbillReceipt.objects.select_for_update().filter(issue_id=issue_id).first()
        if not row or row.next_attempt_at is None:
            return
        if row.next_attempt_at > now and (not retry or row.claim):
            return
        if (
            not enabled()
            or row.environment != settings.BILLING_POPBILL_MODE
            or row.issuer != settings.BILLING_POPBILL_CORP_NUM
        ):
            return
        issue = row.issue
        if not issue.invoice.paid_at or issue.invoice.bank_transfer_notice.status != "CONFIRMED":
            return
        row.claim = lease
        row.next_attempt_at = now + timedelta(minutes=2)
        row.save(update_fields=["claim", "next_attempt_at"])

    result_error = None
    try:
        result = _exchange(row)
        state = int(result.get("state", 0))
        number = result.get("number") or ""
        valid_number = re.fullmatch(r"[A-Za-z0-9]{9}" if row.kind == "CASH" else r"[A-Za-z0-9]{24}", number)
        accepted = state in (300, 301, 302, 303, 304) and valid_number
        result_error = result.get("error")
        reason = (
            f"팝빌 오류 {result_error}" if isinstance(result_error, int) else "팝빌 처리 결과를 다시 확인해 주세요."
        )
        if state == 305:
            reason = "국세청 전송 반려. 팝빌의 해당 문서에서 반려 사유를 확인해 주세요."
    except Exception:
        # The provider may already have signed. Retry only with this same document key.
        state, number, accepted, reason = 0, "", False, "팝빌 처리 결과를 다시 확인해 주세요."

    with transaction.atomic():
        current = PopbillReceipt.objects.select_for_update().filter(pk=row.pk, claim=lease).first()
        if not current:
            return
        issue = TaxInvoiceIssue.objects.select_for_update().get(pk=issue_id)
        current.claim = None
        if state:
            current.provider_state = state
        current.next_attempt_at = timezone.now() + timedelta(minutes=15)
        if accepted:
            issue.status = "ISSUED"
            if current.kind == "CASH":
                current.cash_approval_number = number
            else:
                issue.issue_number = number
            issue.issued_at = issue.issued_at or timezone.now()
            issue.failure_reason = ""
            current.recipient_ciphertext = ""
            if state == 304:
                current.next_attempt_at = None
        else:
            if state == 305:
                current.recipient_ciphertext = ""
            if state or not (current.cash_approval_number or issue.issue_number):
                issue.status = "FAILED" if state or isinstance(result_error, int) else "READY"
            issue.failure_reason = reason
        issue.save(update_fields=["status", "issue_number", "issued_at", "failure_reason", "updated_at"])
        current.save(
            update_fields=[
                "claim",
                "cash_approval_number",
                "recipient_ciphertext",
                "provider_state",
                "next_attempt_at",
                "updated_at",
            ]
        )


def reconcile_due():
    if enabled():
        ids = (
            PopbillReceipt.objects.filter(
                next_attempt_at__lte=timezone.now(),
                environment=settings.BILLING_POPBILL_MODE,
                issuer=settings.BILLING_POPBILL_CORP_NUM,
                issue__invoice__paid_at__isnull=False,
                issue__invoice__bank_transfer_notice__status="CONFIRMED",
            )
            .order_by("next_attempt_at", "pk")
            .values_list("issue_id", flat=True)
        )
        for issue_id in ids[:200]:
            dispatch(issue_id)
