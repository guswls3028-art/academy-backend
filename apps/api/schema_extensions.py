"""drf-spectacular adapters for Academy authentication classes."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class TokenVersionJWTScheme(OpenApiAuthenticationExtension):
    target_class = "apps.core.authentication.TokenVersionJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


class TenantAwareSessionScheme(OpenApiAuthenticationExtension):
    target_class = "apps.core.authentication.TenantAwareSessionAuthentication"
    name = "sessionAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": "sessionid"}
