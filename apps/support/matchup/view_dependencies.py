"""Cross-domain dependency helpers for matchup views."""

from __future__ import annotations


def get_ai_reconcile_models():
    from apps.domains.ai.models import AIJobModel, AIResultModel

    return AIJobModel, AIResultModel


def handle_matchup_ai_result(**kwargs):
    from apps.domains.ai.callbacks import _handle_matchup_ai_result

    return _handle_matchup_ai_result(**kwargs)


def cache_ai_job_status(**kwargs):
    from apps.domains.ai.redis_status_cache import cache_job_status

    return cache_job_status(**kwargs)


def get_inventory_r2_helpers():
    from apps.domains.inventory.r2_path import (
        build_r2_key,
        folder_path_string,
        safe_filename,
    )

    return build_r2_key, safe_filename, folder_path_string


def get_inventory_file_model():
    from apps.domains.inventory.models import InventoryFile

    return InventoryFile
