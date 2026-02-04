# ======================================================================
# PATH: apps/core/tenant/exceptions.py
# ======================================================================
from __future__ import annotations


class TenantResolutionError(Exception):
    """
    Tenant resolution failures are explicit & ops-friendly.

    code:
      - tenant_missing
      - tenant_invalid
      - tenant_inactive
      - tenant_ambiguous
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int = 400,
    ):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.http_status = int(http_status)
