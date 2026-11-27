"""Isolate the SDK's process-wide connection cache and bound each network operation.

This module starts without Django's application registry. Never log request data.
"""

import json
import socket
import sys

from popbill import Cashbill, CashbillService, PopbillException, Taxinvoice, TaxinvoiceService


def exchange(request):
    cash = request["kind"] == "CASH"
    service_type = CashbillService if cash else TaxinvoiceService
    service = service_type(request["link_id"], request["secret_key"])
    service.IsTest = request["mode"] == "test"
    corp = request["corp"]
    key = request["key"]
    args = (corp, key) if cash else (corp, "SELL", key)
    if not service.checkMgtKeyInUse(*args):
        if not request["document"]:
            raise ValueError("Previously issued document missing")
        if cash:
            service.registIssue(corp, Cashbill(**request["document"]), "")
        else:
            service.registIssue(corp, Taxinvoice(**request["document"]))
    info = service.getInfo(*args)
    return {
        "state": int(info.stateCode),
        "number": getattr(info, "confirmNum" if cash else "ntsconfirmNum", "") or "",
    }


if __name__ == "__main__":
    socket.setdefaulttimeout(4)
    try:
        result = exchange(json.load(sys.stdin))
    except PopbillException as error:
        result = {"error": int(error.code)}
    except Exception:
        result = {"error": "transport"}
    print(json.dumps(result))
