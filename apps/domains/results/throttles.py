from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class WrongNotePDFCreateThrottle(SimpleRateThrottle):
    scope = "wrong_note_pdf_create"
    rate = "10/hour"

    def get_cache_key(self, request, view):
        tenant = getattr(request, "tenant", None)
        user = getattr(request, "user", None)
        if not tenant or not user or not user.is_authenticated:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{tenant.pk}:{user.pk}",
        }
