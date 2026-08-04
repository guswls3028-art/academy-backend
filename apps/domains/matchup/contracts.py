"""Public cross-domain read contracts owned by the Matchup domain."""

from __future__ import annotations

from collections.abc import Iterator


def iter_problem_studio_reference_texts(*, tenant_id: int) -> Iterator[str]:
    from .selectors import iter_problem_studio_reference_texts as _impl

    return _impl(tenant_id=tenant_id)


def iter_problem_studio_teacher_comments(*, tenant_id: int) -> Iterator[str]:
    from .selectors import iter_problem_studio_teacher_comments as _impl

    return _impl(tenant_id=tenant_id)
