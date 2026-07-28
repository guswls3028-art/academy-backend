from __future__ import annotations

from collections.abc import Iterator

from .models import MatchupHitReportEntry, MatchupProblem


def iter_problem_studio_reference_texts(*, tenant_id: int) -> Iterator[str]:
    """Yield Matchup problem text through the domain's public read boundary."""
    return (
        MatchupProblem.objects.filter(
            tenant_id=tenant_id,
            source_type="matchup",
        )
        .exclude(text="")
        .values_list("text", flat=True)
        .iterator(chunk_size=500)
    )


def iter_problem_studio_teacher_comments(*, tenant_id: int) -> Iterator[str]:
    """Yield teacher-authored Matchup comments for deidentified style fixtures."""
    return (
        MatchupHitReportEntry.objects.filter(tenant_id=tenant_id)
        .exclude(comment="")
        .values_list("comment", flat=True)
        .iterator(chunk_size=500)
    )
