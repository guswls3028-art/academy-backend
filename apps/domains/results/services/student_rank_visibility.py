"""Project exam comparison fields according to student-facing visibility policy."""


def student_rank_visibility_payload(
    rank_info: dict,
    *,
    rank_published: bool,
    cohort_size_published: bool,
    include_score_benchmarks: bool = False,
) -> dict:
    cohort_size = rank_info.get("cohort_size")
    comparable_cohort = isinstance(cohort_size, int) and cohort_size > 1
    can_show_rank = rank_published and comparable_cohort
    payload = {
        "rank": rank_info.get("rank") if can_show_rank else None,
        # Rank / percentile would disclose the hidden cohort size.
        "percentile": rank_info.get("percentile") if can_show_rank and cohort_size_published else None,
        "cohort_size": cohort_size if cohort_size_published else None,
        "cohort_avg": rank_info.get("cohort_avg") if can_show_rank else None,
    }
    if include_score_benchmarks:
        payload.update({
            "cohort_top_decile_avg": rank_info.get("cohort_top_decile_avg") if can_show_rank else None,
            "cohort_highest": rank_info.get("cohort_highest") if can_show_rank else None,
        })
    return payload
