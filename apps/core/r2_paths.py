# PATH: apps/core/r2_paths.py
"""
R2 객체 키 SSOT — 멀티테넌트 + Aurora 확장 대비

모든 R2 키는 tenants/{tenant_id}/... 로 통일 (storage 인벤토리와 동일 패턴).
- Video: tenants/{tenant_id}/video/raw|hls/...
- AI:    tenants/{tenant_id}/ai/submissions|exams/...
- Storage(인벤토리): apps.domains.inventory.r2_path.build_r2_key (기존 유지)
"""

from __future__ import annotations


def video_raw_key(
    *,
    tenant_id: int,
    session_id: int,
    unique_id: str,
    ext: str,
) -> str:
    """원본 영상 업로드 키 (presigned PUT 대상)."""
    return f"tenants/{tenant_id}/video/raw/{session_id}/{unique_id}.{ext}"


def video_hls_prefix(tenant_id: int, video_id: int) -> str:
    """HLS 출력 prefix (master.m3u8, thumbnail.jpg, v1/index.m3u8 등이 이 아래에)."""
    return f"tenants/{tenant_id}/video/hls/{video_id}"


def video_hls_master_path(tenant_id: int, video_id: int) -> str:
    """HLS 마스터 플레이리스트 상대 경로 (DB hls_path / CDN path)."""
    return f"tenants/{tenant_id}/video/hls/{video_id}/master.m3u8"


def ai_submission_key(
    *,
    tenant_id: int,
    submission_id: int,
    unique_id: str,
    ext: str,
) -> str:
    """제출물 파일 키 (AI 버킷)."""
    return f"tenants/{tenant_id}/ai/submissions/{submission_id}/{unique_id}.{ext}"


def ai_exam_asset_key(
    *,
    tenant_id: int,
    exam_id: int,
    asset_type: str,
    unique_id: str,
    ext: str,
) -> str:
    """시험 자산 파일 키 (OMR 시트, 문제 PDF 등)."""
    return f"tenants/{tenant_id}/ai/exams/{exam_id}/assets/{asset_type}/{unique_id}.{ext}"
