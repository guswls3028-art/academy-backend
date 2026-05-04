"""find_similar_problems 격리 정책 회귀 테스트.

학원장 실측 갭 fix (2026-05-05):
  기존: 모든 source 가 같은 카테고리 안에서만 추천 → 박철T 처럼 카테고리당 doc 1~몇 개
        분포면 시험지 source 의 매칭 풀 0이 강제. 매치업 자동 추천 작동률 0%.
  변경: 시험지 source(school_exam_pdf / student_exam_photo) 는 카테고리 격리 해제.
        자료 source 끼리 매칭은 카테고리 격리 유지.

회귀 락:
- 시험지 source 가 다른 카테고리의 reference 자료를 후보로 받을 수 있어야 한다.
- 자료 source 는 여전히 같은 카테고리만 후보로 받아야 한다 (회귀 방지).
- author_id 격리는 둘 다 유지 (강사 1인 격리 SSOT 보존).
"""
from __future__ import annotations

import uuid

import pytest

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.services import find_similar_problems
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(code="t-isolate", name="격리 테스트")


@pytest.fixture
def author(db, tenant):
    return User.objects.create_user(
        username="author1",
        password="x",
        tenant=tenant,
    )


def _make_inventory_file(*, tenant) -> InventoryFile:
    suffix = uuid.uuid4().hex[:12]
    return InventoryFile.objects.create(
        tenant_id=tenant.id,
        scope="admin",
        display_name=f"테스트 자료 {suffix}",
        r2_key=f"test/{suffix}.pdf",
        original_name=f"{suffix}.pdf",
        content_type="application/pdf",
    )


def _make_problem(
    *,
    tenant,
    author,
    category: str,
    source_type: str,
    text: str,
    embedding: list[float],
    doc_title: str,
    problem_number: int = 1,
):
    inv = _make_inventory_file(tenant=tenant)
    suffix = uuid.uuid4().hex[:8]
    doc = MatchupDocument.objects.create(
        tenant_id=tenant.id,
        author=author,
        inventory_file=inv,
        title=doc_title,
        category=category,
        status="done",
        r2_key=f"matchup/{suffix}.pdf",
        original_name=f"{suffix}.pdf",
        meta={"source_type": source_type},
    )
    return MatchupProblem.objects.create(
        tenant_id=tenant.id,
        document=doc,
        number=problem_number,
        text=text,
        embedding=embedding,
        meta={},
    )


def _emb(seed: float) -> list[float]:
    """768차원 dummy embedding — seed로 다양성."""
    return [seed * (i % 7 + 1) * 0.01 for i in range(768)]


@pytest.mark.django_db
def test_test_paper_source_crosses_categories(tenant, author):
    """시험지 source 는 다른 카테고리 자료를 후보로 받아야 한다 (학원장 실측 갭 fix)."""
    # 시험지 source — "2026 중대부고 1학기 중간고사" 카테고리
    test_problem = _make_problem(
        tenant=tenant,
        author=author,
        category="2026 중대부고 1학기 중간고사",
        source_type="student_exam_photo",
        text="다음 중 옳은 것은?",
        embedding=_emb(1.0),
        doc_title="2026 중대부고 시험지",
    )
    # 강사 본인의 다른 카테고리 자료 — "박철T 언남 생명 매치업"
    ref_problem = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 언남 생명 매치업",
        source_type="academy_workbook",
        text="다음 중 옳은 것은?",
        embedding=_emb(1.01),
        doc_title="박철T 워크북 1회차",
    )

    # author_id 격리 분리 위해 None으로 먼저 — 후보 풀 자체에 들어가는지 확인.
    results_no_author = find_similar_problems(
        problem_id=test_problem.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=None,
    )
    assert len(results_no_author) >= 1, (
        "카테고리 격리 해제만으로 시험지 source 가 다른 카테고리 자료를 후보로 받아야 함 "
        f"(현재 후보={len(results_no_author)})"
    )

    results = find_similar_problems(
        problem_id=test_problem.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=author.id,
    )

    assert len(results) >= 1, "시험지 source 가 다른 카테고리 자료를 후보로 받아야 함"
    found_ids = [p.id for p, _ in results]
    assert ref_problem.id in found_ids, "다른 카테고리의 강사 본인 자료가 후보 풀에 포함되어야 함"


@pytest.mark.django_db
def test_reference_source_stays_within_category(tenant, author):
    """자료 source 는 같은 카테고리만 후보로 받아야 한다 (기존 정책 유지)."""
    # 자료 source — academy_workbook
    ref_a = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 언남 생명 매치업",
        source_type="academy_workbook",
        text="문제 본문 A",
        embedding=_emb(2.0),
        doc_title="박철T 워크북 회차1",
    )
    # 같은 카테고리 자료
    ref_same_cat = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 언남 생명 매치업",
        source_type="academy_workbook",
        text="문제 본문 A 유사",
        embedding=_emb(2.01),
        doc_title="박철T 워크북 회차2",
    )
    # 다른 카테고리 자료 — 후보에서 빠져야 함
    ref_other_cat = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 개포 생명 매치업",
        source_type="academy_workbook",
        text="문제 본문 A 유사",
        embedding=_emb(2.02),
        doc_title="박철T 워크북 다른카테고리",
    )

    results = find_similar_problems(
        problem_id=ref_a.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=author.id,
    )

    found_ids = {p.id for p, _ in results}
    assert ref_same_cat.id in found_ids, "자료 source 는 같은 카테고리 자료를 후보로 받아야 함"
    assert ref_other_cat.id not in found_ids, (
        "자료 source 는 다른 카테고리 자료를 후보에서 제외해야 함 (회귀 락)"
    )


@pytest.mark.django_db
def test_test_paper_self_doc_excluded(tenant, author):
    """시험지 source 는 자기 doc 안 problem 을 후보에서 제외해야 한다 (self-doc trap 방지)."""
    inv = _make_inventory_file(tenant=tenant)
    suffix = uuid.uuid4().hex[:8]
    test_doc = MatchupDocument.objects.create(
        tenant_id=tenant.id,
        author=author,
        inventory_file=inv,
        title="시험지",
        category="2026 중대부고 1학기 중간고사",
        status="done",
        r2_key=f"matchup/{suffix}.pdf",
        original_name=f"{suffix}.pdf",
        meta={"source_type": "student_exam_photo"},
    )
    p1 = MatchupProblem.objects.create(
        tenant_id=tenant.id,
        document=test_doc,
        number=1,
        text="문제 1",
        embedding=_emb(3.0),
        meta={},
    )
    p2_same_doc = MatchupProblem.objects.create(
        tenant_id=tenant.id,
        document=test_doc,
        number=2,
        text="문제 1",  # 동일 텍스트 — sim≈1
        embedding=_emb(3.0),
        meta={},
    )
    # 다른 doc 의 자료 — 후보로 떠야 함
    ref_other_doc = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 워크북",
        source_type="academy_workbook",
        text="문제 1",
        embedding=_emb(3.01),
        doc_title="다른 doc",
    )

    results = find_similar_problems(
        problem_id=p1.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=author.id,
    )

    found_ids = {p.id for p, _ in results}
    assert p2_same_doc.id not in found_ids, "self-doc trap — 자기 doc problem 은 후보에서 제외"
    assert ref_other_doc.id in found_ids, "다른 doc 의 자료는 후보로 떠야 함"


@pytest.mark.django_db
def test_author_isolation_preserved(tenant):
    """author_id 격리는 카테고리 격리 변경 후에도 유지되어야 한다."""
    author_a = User.objects.create_user(username="A", password="x", tenant=tenant)
    author_b = User.objects.create_user(username="B", password="x", tenant=tenant)

    test_a = _make_problem(
        tenant=tenant,
        author=author_a,
        category="2026 중대부고 1학기 중간고사",
        source_type="student_exam_photo",
        text="문제",
        embedding=_emb(4.0),
        doc_title="A 시험지",
    )
    # B 강사의 자료 — A 검색에서 빠져야 함
    ref_b = _make_problem(
        tenant=tenant,
        author=author_b,
        category="B 강사 워크북",
        source_type="academy_workbook",
        text="문제",
        embedding=_emb(4.01),
        doc_title="B 워크북",
    )

    results = find_similar_problems(
        problem_id=test_a.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=author_a.id,
    )

    found_ids = {p.id for p, _ in results}
    assert ref_b.id not in found_ids, "다른 강사 자료는 후보 풀에서 제외 (강사 1인 격리 SSOT)"
