"""Project exam comparison fields according to student-facing visibility policy."""


def student_rank_visibility_payload(
    rank_info: dict,
    *,
    rank_published: bool,
    cohort_size_published: bool,
) -> dict:
    cohort_size = rank_info.get("cohort_size")
    comparable_cohort = isinstance(cohort_size, int) and cohort_size > 1
    can_show_rank = rank_published and comparable_cohort
    return {
        "rank": rank_info.get("rank") if can_show_rank else None,
        "percentile": rank_info.get("percentile") if can_show_rank else None,
        "cohort_size": cohort_size if cohort_size_published else None,
        "cohort_avg": rank_info.get("cohort_avg") if can_show_rank else None,
    }
