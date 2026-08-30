"""Shared database expressions for deterministic student-name ordering."""

from collections.abc import Sequence

from django.db import connection
from django.db.models import F
from django.db.models.functions import Collate


def student_name_codepoint_ordering(
    ordering: Sequence[str],
    *,
    name_field: str,
):
    """Use UTF-8 codepoint order on PostgreSQL and native binary order elsewhere."""
    if connection.vendor != "postgresql":
        return list(ordering)

    result = []
    for field in ordering:
        descending = field.startswith("-")
        if field.lstrip("-") != name_field:
            result.append(field)
            continue

        expression = Collate(F(name_field), "C")
        result.append(expression.desc() if descending else expression.asc())
    return result
