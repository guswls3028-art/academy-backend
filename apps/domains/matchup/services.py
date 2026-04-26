# PATH: apps/domains/matchup/services.py
# 매치업 비즈니스 로직 — 유사도 검색, R2 정리, 재시도

from __future__ import annotations

import logging
import os
from typing import List, Tuple

from apps.shared.utils.vector import cosine_similarity
from .models import MatchupDocument, MatchupProblem

logger = logging.getLogger(__name__)

try:
    from apps.infrastructure.storage.r2 import delete_object_r2_storage
except ImportError:
    delete_object_r2_storage = None  # type: ignore


# ── Heuristic reranker 가중치 ───────────────────────────
#
# V2 측정(15 케이스)에서 발견된 부작용으로 V2.5 보수화:
#  - format_match=0.12가 같은 시험지 essay-essay 트랩을 강화 → 0.0
#  - length_norm=0.06이 정제 후 짧아진 텍스트에 부정적 영향 → 0.0
#  - sim 비중 ↑, cross_doc만 살려 서답형 트랩 약화 (다른 시험지 우선)
# 휴리스틱은 여기까지. 80%+ 도약은 cross-encoder reranker (Phase 2)에서.
_W_SIM = 1.0         # V2.6: 휴리스틱 전부 비활성 — 직접 측정에서 휴리스틱이
_W_FORMAT = 0.0      #        top1 외에 top2/3 회복을 망침. 순수 sim으로 회귀.
_W_LENGTH = 0.0      #
_W_CROSS_DOC = 0.0   #

# Phase 2 cross-encoder 토글 (기본 OFF).
# bge-reranker-base는 한국어 시험 문제 의미를 잘 못 잡아 V2.6 56% → 40% 후퇴.
# v2-m3-ko로 재시도하려면 EBS 8GB→20GB 확장 필요.
# 운영 중 활성화: SSM에서 환경변수 MATCHUP_USE_CROSS_ENCODER=1 + ASG refresh.
_USE_CROSS_ENCODER = os.environ.get("MATCHUP_USE_CROSS_ENCODER", "0") == "1"


def _format_of(problem: MatchupProblem) -> str:
    """problem의 meta에서 format 추출. 미설정이면 텍스트로 즉석 감지(레거시)."""
    meta = problem.meta or {}
    fmt = meta.get("format")
    if fmt in ("essay", "choice"):
        return fmt
    text = problem.text or ""
    return "essay" if any(
        marker in text[:20] for marker in ("[서답형", "[ 서답형", "[서 답형", "[ 서 답형", "서논술형")
    ) else "choice"


def _length_score(src_len: int, cand_len: int) -> float:
    """텍스트 길이 비율 점수. 비슷한 길이일수록 1.0, 차이 클수록 0."""
    if src_len <= 0 or cand_len <= 0:
        return 0.5  # 정보 부족 — 중립
    short, long_ = sorted([src_len, cand_len])
    return short / long_


def find_similar_problems(
    problem_id: int, tenant_id: int, top_k: int = 10
) -> List[Tuple["MatchupProblem", float]]:
    """주어진 문제와 유사한 문제를 찾아 재정렬해 반환.

    Pipeline:
      1. bi-encoder cosine으로 후보 점수화 (DB의 embedding)
      2. 휴리스틱 신호(sim·cross_doc) 결합 → 1차 정렬
      3. (가능 시) cross-encoder reranker로 상위 후보 재정렬 — phase 2
      4. top_k 반환

    Returns: [(problem, final_score), ...] 높은 순.
    """
    try:
        source = MatchupProblem.objects.get(id=problem_id, tenant_id=tenant_id)
    except MatchupProblem.DoesNotExist:
        return []

    if not source.embedding:
        return []

    source_category = ""
    if source.document_id and source.document is not None:
        source_category = (source.document.category or "").strip()

    candidates = (
        MatchupProblem.objects
        .filter(tenant_id=tenant_id, embedding__isnull=False)
        .exclude(id=problem_id)
        .defer("created_at", "updated_at")
    )

    # 시험지(test) source의 자기 doc 안 problem은 후보에서 제외.
    # 같은 시험지의 다른 problem이 동일 OCR 텍스트로 인덱싱돼 sim≈1로 잡히는
    # self-doc trap 차단. reference doc 간 cross-doc 추천에는 영향 없음.
    if source.document_id and source.document is not None:
        meta = source.document.meta or {}
        intent = (meta.get("upload_intent") or "").lower()
        role = (meta.get("document_role") or "").lower()
        if intent == "test" or role == "exam_sheet":
            candidates = candidates.exclude(document_id=source.document_id)

    # 같은 카테고리(섹션) 내에서만 추천.
    # source가 matchup 문서인 경우에만 적용 (exam source는 document가 None).
    if source_category:
        candidates = candidates.filter(
            document__isnull=False,
            document__category=source_category,
        )

    src_format = _format_of(source)
    src_len = len(source.text or "")
    src_doc_id = source.document_id

    # 1차: bi-encoder + 가벼운 휴리스틱
    scored = []
    for c in candidates:
        if not c.embedding:
            continue
        sim = cosine_similarity(source.embedding, c.embedding)

        fmt_match = 1.0 if _format_of(c) == src_format else 0.0
        len_score = _length_score(src_len, len(c.text or ""))
        cross_doc = 1.0 if c.document_id != src_doc_id else 0.0

        final = (
            _W_SIM * sim
            + _W_FORMAT * fmt_match
            + _W_LENGTH * len_score
            + _W_CROSS_DOC * cross_doc
        )
        scored.append((c, final))

    scored.sort(key=lambda x: x[1], reverse=True)

    # 2차: cross-encoder reranking — 환경변수 MATCHUP_USE_CROSS_ENCODER=1 일 때만.
    # 기본 OFF: bge-reranker-base가 한국어 시험 문제에 부적합 확인됨.
    if _USE_CROSS_ENCODER:
        pre_top = scored[:max(top_k * 2, 20)]
        if len(pre_top) >= 2:
            reranked = _rerank_with_cross_encoder(source, pre_top)
            if reranked is not None:
                return reranked[:top_k]

    return scored[:top_k]


def _rerank_with_cross_encoder(source, pre_top):
    """Cross-encoder로 pre_top 재정렬. 의존성 없거나 실패 시 None.

    Returns: [(problem, score), ...] 또는 None
    """
    try:
        from . import reranker as rr
    except ImportError:
        return None
    cands_text = [(p.text or "") for p, _ in pre_top]
    rr_result = rr.rerank(source.text or "", cands_text, top_k=len(pre_top))
    if rr_result is None:
        return None
    return [(pre_top[idx][0], float(score)) for idx, score in rr_result]


def cleanup_matchup_problem_images(document: MatchupDocument) -> int:
    """매치업 문서의 problem 이미지를 R2에서 삭제. 원본 PDF/이미지는 건드리지 않음.

    호출 컨텍스트:
      1. 매치업 문서 직접 삭제 (delete_document_with_r2 내부에서)
      2. InventoryFile 삭제 cascade 직전 (R2 orphan 방지)

    Returns: 삭제 시도한 problem 이미지 개수.
    """
    problem_keys = list(
        document.problems.exclude(image_key="").values_list("image_key", flat=True)
    )
    # 수동 크롭 모달이 PDF 페이지를 R2에 캐시했다면 함께 정리 — orphan 방지.
    page_cache_keys = list((document.meta or {}).get("page_image_keys") or [])
    all_keys = [k for k in (problem_keys + page_cache_keys) if k]

    if not delete_object_r2_storage:
        return 0
    for key in all_keys:
        try:
            delete_object_r2_storage(key=key)
        except Exception:
            logger.warning("R2 delete failed: %s", key, exc_info=True)
    return len(all_keys)


def delete_document_with_r2(document: MatchupDocument) -> None:
    """매치업 문서 삭제 — 문제 크롭 이미지만 R2에서 제거.

    원본 PDF/이미지(document.r2_key)는 InventoryFile이 소유하므로 여기서 지우지 않는다.
    원본 삭제는 InventoryFile 삭제 시 이루어지고 CASCADE로 MatchupDocument도 함께 삭제된다.
    """
    cleanup_matchup_problem_images(document)
    document.delete()  # CASCADE로 problems도 삭제 (InventoryFile은 그대로)


def retry_document(document: MatchupDocument) -> str:
    """실패한 문서를 재처리. 새 AI job을 디스패치하고 job_id 반환."""
    from apps.domains.ai.gateway import dispatch_job
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    # 기존 문제 삭제
    document.problems.all().delete()

    download_url = generate_presigned_get_url_storage(
        key=document.r2_key, expires_in=3600
    )

    result = dispatch_job(
        job_type="matchup_analysis",
        payload={
            "download_url": download_url,
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "filename": document.original_name,
        },
        tenant_id=str(document.tenant_id),
        source_domain="matchup",
        source_id=str(document.id),
    )

    if isinstance(result, dict) and not result.get("ok", True):
        raise RuntimeError(result.get("error", "dispatch failed"))

    job_id = result.get("job_id", "") if isinstance(result, dict) else str(result)
    document.status = "processing"
    document.ai_job_id = str(job_id)
    document.error_message = ""
    document.problem_count = 0
    document.save(update_fields=["status", "ai_job_id", "error_message", "problem_count", "updated_at"])

    return job_id


# ── Storage-as-canonical helpers ─────────────────────────
#
# 멘탈 모델: InventoryFile = canonical 자료. MatchupDocument = 그 위의 분석 레이어.
# 매치업 자료의 진입점은 두 가지:
#   1. 매치업 페이지에서 업로드 → InventoryFile 생성 + 즉시 승격 (1-step UX)
#   2. 저장소에서 우클릭/토글 → 기존 InventoryFile 승격
# 두 경로 모두 promote_inventory_to_matchup으로 수렴.

MATCHUP_UPLOAD_ROOT = "매치업-업로드"


def ensure_matchup_upload_folder(tenant):
    """매치업 페이지 직접 업로드용 폴더 (/매치업-업로드/{YYYY-MM}/) 자동 생성. Returns InventoryFolder."""
    from apps.domains.inventory.models import InventoryFolder
    from datetime import datetime

    root, _ = InventoryFolder.objects.get_or_create(
        tenant=tenant, scope="admin", student_ps="",
        parent=None, name=MATCHUP_UPLOAD_ROOT,
    )
    ym_key = datetime.now().strftime("%Y-%m")
    ym_folder, _ = InventoryFolder.objects.get_or_create(
        tenant=tenant, scope="admin", student_ps="",
        parent=root, name=ym_key,
    )
    return ym_folder


_AUTO_CATEGORY_SKIP_FOLDERS = {
    # 매치업 자체 폴더 — 사용자가 의도한 "학교/세트" 카테고리가 아님.
    "매치업-자동등록", "매치업-업로드",
}


def _infer_category_from_folder(inventory_file) -> str:
    """inventory_file의 부모 폴더명에서 category 추출.

    사용자의 mental model: 저장소 폴더 = 학교/세트 = 매치업 카테고리.
    `/중대부고/2026 1학기/시험지.pdf` → category="중대부고".
    매치업 시스템 폴더(매치업-업로드 등)는 무시.
    """
    folder = getattr(inventory_file, "folder", None)
    if folder is None:
        return ""
    # 가장 가까운 의미있는 부모 폴더를 거슬러 올라가며 찾는다.
    cur = folder
    seen = 0
    while cur is not None and seen < 8:
        name = (cur.name or "").strip()
        if name and name not in _AUTO_CATEGORY_SKIP_FOLDERS:
            # YYYY-MM 형식(매치업-업로드 하위)도 의미없는 카테고리 — 스킵.
            if not (len(name) == 7 and name[4] == "-" and name[:4].isdigit()):
                return name[:100]  # 모델 max_length=100
        cur = getattr(cur, "parent", None)
        seen += 1
    return ""


def promote_inventory_to_matchup(
    inventory_file,
    *,
    title: str = "",
    category: str = "",
    subject: str = "",
    grade_level: str = "",
):
    """InventoryFile을 매치업 분석 대상으로 승격. Returns MatchupDocument.

    중복 승격 검사는 호출 측 책임 (트랜잭션 내 select_for_update 또는 IntegrityError 처리).
    OneToOneField unique 제약으로 DB 레벨에서도 race 차단.

    category가 비어 있으면 저장소 폴더 트리에서 자동 추론 — 사용자가 폴더로
    이미 분류해둔 mental model을 그대로 매치업으로 가져온다.
    """
    from apps.domains.ai.gateway import dispatch_job
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    if not (category or "").strip():
        category = _infer_category_from_folder(inventory_file)

    doc = MatchupDocument.objects.create(
        tenant=inventory_file.tenant,
        inventory_file=inventory_file,
        title=title or inventory_file.display_name,
        category=category,
        subject=subject,
        grade_level=grade_level,
        r2_key=inventory_file.r2_key,
        original_name=inventory_file.original_name,
        size_bytes=inventory_file.size_bytes,
        content_type=inventory_file.content_type,
        status="pending",
        meta={},
    )

    try:
        download_url = generate_presigned_get_url_storage(
            key=inventory_file.r2_key, expires_in=3600,
        )
        result = dispatch_job(
            job_type="matchup_analysis",
            payload={
                "download_url": download_url,
                "tenant_id": str(inventory_file.tenant_id),
                "document_id": str(doc.id),
                "filename": inventory_file.original_name,
            },
            tenant_id=str(inventory_file.tenant_id),
            source_domain="matchup",
            source_id=str(doc.id),
        )
        if isinstance(result, dict) and not result.get("ok", True):
            raise RuntimeError(result.get("error", "dispatch failed"))
        job_id = result.get("job_id", "") if isinstance(result, dict) else str(result)
        doc.status = "processing"
        doc.ai_job_id = str(job_id)
        doc.save(update_fields=["status", "ai_job_id", "updated_at"])
    except Exception:
        logger.exception("Failed to dispatch matchup_analysis for doc %s", doc.id)
        doc.status = "failed"
        doc.error_message = "AI 분석 작업 생성에 실패했습니다."
        doc.save(update_fields=["status", "error_message", "updated_at"])

    return doc


# ── 수동 크롭 ────────────────────────────────────────────
#
# 자동 분리 결과가 처참할 때 사용자가 직접 박스를 그려 problem을 추가/수정한다.
# 즉시 반영이 핵심 — embedding 계산은 비동기 워커에 위임하되 problem record와
# 이미지 업로드는 동기 처리해 사용자 화면에 즉각 노출.

def _download_inventory_to_temp(inventory_file) -> str:
    """InventoryFile R2 객체를 임시 파일로 다운로드. 호출자가 cleanup 책임.

    Returns: 로컬 임시 파일 경로.
    """
    import tempfile
    import os

    from apps.infrastructure.storage.r2 import (
        generate_presigned_get_url_storage,
    )

    url = generate_presigned_get_url_storage(
        key=inventory_file.r2_key, expires_in=600,
    )
    if not url:
        raise RuntimeError("presigned URL 생성 실패")

    import urllib.request
    suffix = os.path.splitext(inventory_file.original_name or "")[1] or ".bin"
    fd, path = tempfile.mkstemp(prefix="matchup-manual-", suffix=suffix)
    os.close(fd)
    urllib.request.urlretrieve(url, path)
    return path


def manually_crop_problem(
    document: MatchupDocument,
    *,
    page_index: int,
    bbox_norm: Tuple[float, float, float, float],
    number: int,
    text: str = "",
) -> MatchupProblem:
    """document의 page_index 페이지에서 bbox_norm 영역을 잘라 새 problem 등록.

    bbox_norm: (x, y, w, h), 모두 0..1 (페이지 크기로 정규화).
    같은 number의 problem이 이미 있으면 이미지·meta를 갱신해 덮어쓴다.

    동기 처리 흐름:
      1. R2에서 원본 PDF/이미지 다운로드 (임시 파일)
      2. PDF면 PyMuPDF로 페이지 렌더, 이미지면 그대로 PIL 로드
      3. bbox_norm을 픽셀 좌표로 변환 → PIL crop → PNG bytes
      4. R2 업로드 (matchup problem key)
      5. MatchupProblem upsert (embedding은 비어둠 — 워커가 채움)

    Returns: 생성/갱신된 MatchupProblem.
    """
    import io
    import os
    import tempfile
    from PIL import Image

    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    from .r2_path import build_matchup_problem_key

    if document.inventory_file_id is None:
        raise ValueError("문서에 원본 파일이 연결되어 있지 않습니다.")
    if not (1 <= number <= 999):
        raise ValueError("문항 번호는 1~999 사이여야 합니다.")
    x, y, w, h = bbox_norm
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("bbox는 0~1 범위로 정규화되어야 합니다.")
    if x + w > 1.001 or y + h > 1.001:
        raise ValueError("bbox가 페이지 범위를 벗어납니다.")

    inv_file = document.inventory_file
    local_path = _download_inventory_to_temp(inv_file)
    try:
        is_pdf = (
            (inv_file.content_type or "").lower() == "application/pdf"
            or (inv_file.original_name or "").lower().endswith(".pdf")
        )
        if is_pdf:
            from academy.adapters.tools.pymupdf_renderer import PdfDocument

            with PdfDocument(local_path) as doc_pdf:
                if page_index < 0 or page_index >= doc_pdf.page_count():
                    raise ValueError(
                        f"page_index {page_index}가 페이지 범위를 벗어납니다 "
                        f"(0~{doc_pdf.page_count() - 1})"
                    )
                page_img = doc_pdf.render_page(page_index, dpi=200)
        else:
            if page_index != 0:
                raise ValueError("이미지 문서는 page_index=0만 가능합니다.")
            page_img = Image.open(local_path).convert("RGB")

        pw, ph = page_img.size
        left = max(0, int(round(x * pw)))
        top = max(0, int(round(y * ph)))
        right = min(pw, int(round((x + w) * pw)))
        bottom = min(ph, int(round((y + h) * ph)))
        if right - left < 5 or bottom - top < 5:
            raise ValueError("선택 영역이 너무 작습니다.")
        crop = page_img.crop((left, top, right, bottom))

        buf = io.BytesIO()
        crop.save(buf, "PNG")
        buf.seek(0)

        # r2 키 prefix 추출 — 기존 문서 key에서 uuid prefix 재사용.
        # 패턴: tenants/{tid}/matchup/{uuid}/<filename>
        # 없으면 tenant 매치업 폴더 prefix를 새로 생성하지 않고 inventory r2 key 옆에 problems/ 디렉터리.
        from .r2_path import build_matchup_document_key  # noqa: F401  (typing only)

        prefix = ""
        parts = (document.r2_key or "").split("/")
        if len(parts) >= 4 and parts[2] == "matchup":
            prefix = parts[3]
        if not prefix:
            # storage-as-canonical 경로(tenants/{tid}/admin/inventory/...)인 경우
            # document별 안정 prefix가 필요 — doc id로 대체.
            prefix = f"manual-{document.id}"

        problem_key = build_matchup_problem_key(
            tenant_id=document.tenant_id, uuid_prefix=prefix, number=number,
        )
        upload_fileobj_to_r2_storage(
            fileobj=buf, key=problem_key, content_type="image/png",
        )

        meta_payload = {
            "manual": True,
            "page_index": int(page_index),
            "bbox_norm": [float(x), float(y), float(w), float(h)],
            "format": "choice",  # 사용자가 명시 안 하면 기본 choice
        }
        problem, created = MatchupProblem.objects.update_or_create(
            tenant=document.tenant,
            document=document,
            number=number,
            defaults={
                "text": text or "",
                "image_key": problem_key,
                "embedding": None,
                "meta": meta_payload,
                "source_type": "matchup",
            },
        )

        # 문서의 problem_count 갱신
        document.problem_count = document.problems.count()
        document.status = "done"
        document.save(update_fields=["problem_count", "status", "updated_at"])

        # NOTE: 임베딩은 비워둠 — 매치업 검색(find_similar_problems)에는 노출되지 않지만
        # 그리드/캔버스/탐색에는 즉시 보임. 검색 인덱싱은 향후 별도 워커 job으로 추가.

        logger.info(
            "MATCHUP_MANUAL_CROP | doc=%s | num=%s | created=%s | bbox=%s",
            document.id, number, created, bbox_norm,
        )
        return problem
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def ensure_document_page_images(document: MatchupDocument) -> List[dict]:
    """문서의 페이지별 이미지를 R2에 캐싱하고 presigned URL 반환.

    수동 크롭 모달에서 캔버스에 그릴 때 필요. 한 번 캐시되면 doc.meta에 보존돼
    다음 호출 시 PDF 다운로드/렌더 없이 바로 presign만 재계산.

    Returns: [{index, url, width, height}, ...] (page 순서)
    """
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    meta = dict(document.meta or {})
    page_keys = meta.get("page_image_keys")
    page_dims = meta.get("page_dimensions") or []  # [(w, h), ...]

    if not page_keys:
        # 캐시 미스: 원본 다운로드 + 페이지 렌더 + R2 업로드.
        page_keys, page_dims = _render_and_upload_pages(document)
        meta["page_image_keys"] = page_keys
        meta["page_dimensions"] = page_dims
        document.meta = meta
        document.save(update_fields=["meta", "updated_at"])

    pages = []
    for i, key in enumerate(page_keys):
        url = generate_presigned_get_url_storage(key=key, expires_in=900)
        w, h = (page_dims[i] if i < len(page_dims) else (0, 0))
        pages.append({"index": i, "url": url, "width": w, "height": h})
    return pages


def _render_and_upload_pages(
    document: MatchupDocument,
) -> Tuple[List[str], List[Tuple[int, int]]]:
    """원본 PDF/이미지를 페이지별 PNG로 잘라 R2 업로드.

    Returns: (page_keys, page_dims) — 같은 길이.
    """
    import io
    import os
    from PIL import Image

    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    inv_file = document.inventory_file
    if inv_file is None:
        raise RuntimeError("inventory_file이 없습니다.")

    local_path = _download_inventory_to_temp(inv_file)
    try:
        is_pdf = (
            (inv_file.content_type or "").lower() == "application/pdf"
            or (inv_file.original_name or "").lower().endswith(".pdf")
        )

        # r2 prefix 결정
        prefix = ""
        parts = (document.r2_key or "").split("/")
        if len(parts) >= 4 and parts[2] == "matchup":
            prefix = parts[3]
        if not prefix:
            prefix = f"manual-{document.id}"

        page_keys: List[str] = []
        page_dims: List[Tuple[int, int]] = []

        if is_pdf:
            from academy.adapters.tools.pymupdf_renderer import PdfDocument

            with PdfDocument(local_path) as doc_pdf:
                for i in range(doc_pdf.page_count()):
                    page_img = doc_pdf.render_page(i, dpi=150)  # 캔버스용은 150 충분
                    buf = io.BytesIO()
                    page_img.save(buf, "PNG")
                    buf.seek(0)
                    key = f"tenants/{document.tenant_id}/matchup/{prefix}/pages/{i:03d}.png"
                    upload_fileobj_to_r2_storage(
                        fileobj=buf, key=key, content_type="image/png",
                    )
                    page_keys.append(key)
                    page_dims.append(page_img.size)
        else:
            img = Image.open(local_path).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            key = f"tenants/{document.tenant_id}/matchup/{prefix}/pages/000.png"
            upload_fileobj_to_r2_storage(
                fileobj=buf, key=key, content_type="image/png",
            )
            page_keys.append(key)
            page_dims.append(img.size)

        return page_keys, page_dims
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def delete_problem_with_r2(problem: MatchupProblem) -> None:
    """단일 problem 삭제 + R2 cleanup.

    수동 추가/자동 추출 모두 동일하게 처리.
    """
    if problem.image_key and delete_object_r2_storage:
        try:
            delete_object_r2_storage(key=problem.image_key)
        except Exception:
            logger.warning(
                "R2 problem image delete failed: %s", problem.image_key,
                exc_info=True,
            )
    doc_id = problem.document_id
    problem.delete()
    if doc_id:
        try:
            doc = MatchupDocument.objects.get(id=doc_id)
            doc.problem_count = doc.problems.count()
            doc.save(update_fields=["problem_count", "updated_at"])
        except MatchupDocument.DoesNotExist:
            pass
