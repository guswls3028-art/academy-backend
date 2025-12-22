# apps/core/middleware/tenant.py

class TenantMiddleware:
    """
    Tenant middleware (placeholder)
    현재는 멀티테넌시 로직 없이 통과만 시킨다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
