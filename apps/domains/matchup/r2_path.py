# PATH: apps/domains/matchup/r2_path.py
# 매치업 R2 저장 경로 SSOT
# 문서: tenants/{tenant_id}/matchup/{uuid}/{original_filename}
# 문제 이미지: tenants/{tenant_id}/matchup/{uuid}/problems/{number}.png

import uuid as _uuid


def build_matchup_document_key(
    *, tenant_id: int, original_name: str
) -> tuple[str, str]:
    """
    매치업 문서의 R2 키와 UUID prefix를 반환.
    Returns: (r2_key, uuid_prefix)
    """
    prefix = str(_uuid.uuid4())
    r2_key = f"tenants/{tenant_id}/matchup/{prefix}/{original_name}"
    return r2_key, prefix


def build_matchup_problem_key(
    *, tenant_id: int, uuid_prefix: str, number: int
) -> str:
    """매치업 문제 크롭 이미지의 R2 키."""
    return f"tenants/{tenant_id}/matchup/{uuid_prefix}/problems/{number}.png"
