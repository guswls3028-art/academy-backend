from django.db import connection
from django.db.models import F
from django.db.models.functions import Collate


POSTGRES_UNICODE_CODEPOINT_COLLATION = "C"


def student_name_ordering(field: str = "name", *, descending: bool = False):
    """Return a stable 가나다 name expression across supported databases."""
    expression = F(field)
    if connection.vendor == "postgresql":
        expression = Collate(expression, POSTGRES_UNICODE_CODEPOINT_COLLATION)
    return expression.desc() if descending else expression.asc()
