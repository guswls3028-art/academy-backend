"""find_similar_problems 격리 정책 회귀 테스트.

정책 진화 이력:
  875f63f3 (2026-05-04): 박철T 카테고리당 doc 1~몇 개 → 시험지 source 카테고리 격리
                        해제 (매치업 작동률 0% fix 시도).
  db8ecb77 (2026-05-05): 학원장 실측 — 개포고 시험지에 단대부고 자료 추천되는 cross-school
                        누출 결함 발견. 카테고리 격리 항상 적용으로 복원. 모든 카테고리
                        (개포고/단대부고/숙명여고/중대부고/은광여고/박철T) 자료 22+ 보유
                        실측 확인 → 격리 유지가 정확.

회귀 락 (현재 정책):
- 시험지/자료 source 모두 같은 카테고리 안에서만 후보 풀 구성 (cross-school 차단).
- 시험지 source 는 자기 doc 안 problem 제외 (self-doc trap 방지).
- author_id 격리는 유지 (강사 1인 격리 SSOT).

DB 의존성:
- find_similar_problems 가 `meta__contains` (jsonb @>) 쿼리 사용 → SQLite 미지원.
- CI smoke test (settings.test = SQLite) 환경에서는 모듈 단위 skip.
- PostgreSQL 통합 테스트(settings.test_pg) 환경에서만 실행.
"""
from __future__ import annotations

import uuid

import pytest

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.services import find_similar_problems
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()

# SQLite 는 meta__contains (jsonb @>) 미지원 → PG 환경에서만 실행.
pytestmark = pytest.mark.skipif(
    "sqlite" in settings.DATABASES["default"].get("ENGINE", ""),
    reason="find_similar_problems uses meta__contains (jsonb @>) — PostgreSQL only",
)


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
def test_test_paper_source_isolated_within_category(tenant, author):
    """시험지 source 는 같은 카테고리 안 자료만 후보로 받아야 한다 (db8ecb77 cross-school fix).

    정책 변경 (db8ecb77, 2026-05-05): 학원장 실측 — 개포고 시험지에 단대부고 자료 추천되는
    cross-school 누출 결함 발견. 시험지 source 카테고리 격리 해제 → 항상 적용으로 복원.
    모든 학교 카테고리 자료 22+ 보유 확인됨 → 격리 유지가 정확.
    """
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
    # 강사 본인의 같은 카테고리 자료 — 후보로 떠야 함
    ref_same_cat = _make_problem(
        tenant=tenant,
        author=author,
        category="2026 중대부고 1학기 중간고사",
        source_type="academy_workbook",
        text="다음 중 옳은 것은?",
        embedding=_emb(1.01),
        doc_title="중대부고 풀이 자료",
    )
    # 강사 본인의 다른 카테고리 자료 — 격리로 후보에서 제외되어야 함
    ref_other_cat = _make_problem(
        tenant=tenant,
        author=author,
        category="박철T 언남 생명 매치업",
        source_type="academy_workbook",
        text="다음 중 옳은 것은?",
        embedding=_emb(1.02),
        doc_title="박철T 워크북 1회차",
    )

    results = find_similar_problems(
        problem_id=test_problem.id,
        tenant_id=tenant.id,
        top_k=10,
        author_id=author.id,
    )

    found_ids = {p.id for p, _, _ in results}
    assert ref_same_cat.id in found_ids, "같은 카테고리 자료는 후보로 떠야 함"
    assert ref_other_cat.id not in found_ids, (
        "다른 카테고리 자료는 cross-school 격리로 후보에서 제외 (db8ecb77 회귀 락)"
    )


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

    found_ids = {p.id for p, _, _ in results}
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

    found_ids = {p.id for p, _, _ in results}
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

    found_ids = {p.id for p, _, _ in results}
    assert ref_b.id not in found_ids, "다른 강사 자료는 후보 풀에서 제외 (강사 1인 격리 SSOT)"
