# PATH: apps/core/management/commands/cleanup_e2e_residue.py
"""
E2E 테스트 잔재 데이터 정리 커맨드.

배경:
    운영 테넌트(Tenant 1 / hakwonplus)에 E2E 자동화 스펙이 생성한 학생·게시글·
    메시지 템플릿·매치업 파일이 누적되어 학원 운영 화면의 품질을 저하시킴.

매칭 패턴 (자동화 스펙이 찍은 명백한 지문):
    - "[E2E-\\d+" / "[AUDIT-\\d+" / "[CHAOS-\\d+"
    - "E2E-\\d{6,}" / "AUDIT-CRUD-\\d+"
    - "EDITED-\\d{6}" (내부 테스트 흔적)
    일반적인 "테스트학생" 같은 자연어는 의도적으로 배제 — 운영에서 이름이
    우연히 겹칠 수 있으므로 strict 패턴만 사용한다.

안전장치:
    - 기본 동작은 --dry-run (삭제하지 않음)
    - --tenant-id 미지정 시 거부 (전 테넌트 일괄 정리 금지)
    - 삭제 직전 요약을 표준출력으로 노출

사용:
    python manage.py cleanup_e2e_residue --tenant-id 1 --dry-run
    python manage.py cleanup_e2e_residue --tenant-id 1 --execute
"""
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# 명백한 E2E 지문 — 자연어와 겹치지 않는 식별자 패턴만 허용
RESIDUE_PATTERNS = [
    re.compile(r"\[E2E-\d{6,}"),
    re.compile(r"\[AUDIT-\w*-?\d{6,}"),
    re.compile(r"\[CHAOS-\d{3,}"),
    re.compile(r"^E2E-\d{6,}"),
    re.compile(r"AUDIT-CRUD-\d{6,}"),
    re.compile(r"^EDITED-\d{5,}$"),
    # 괄호 없는 타임스탬프 접두 패턴 — 자연어에 나타날 수 없음
    re.compile(r"^E2E학생\d{6,}"),
    re.compile(r"^E2E\d{6,}"),
    re.compile(r"^CHAOS-\d{6,}"),
]


def matches_residue(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in RESIDUE_PATTERNS)


class Command(BaseCommand):
    help = "Tenant별 E2E 자동화 잔재 데이터(학생·게시글·메시지 템플릿·매치업 문서)를 식별/삭제한다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            required=True,
            help="대상 테넌트 ID (필수). 실수 방지용 — 전 테넌트 일괄 삭제 금지.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="대상만 출력하고 삭제하지 않음 (기본값).",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="실제 삭제 실행. --dry-run 보다 우선.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="각 카테고리별 출력 샘플 상한 (기본 50).",
        )

    def handle(self, *args, **options):
        tenant_id: int = options["tenant_id"]
        execute: bool = options["execute"]
        limit: int = options["limit"]

        # 모델 지연 import (apps 레지스트리 완료 후)
        from apps.domains.students.models import Student
        from apps.domains.community.models.post import PostEntity
        from apps.domains.matchup.models import MatchupDocument
        from apps.domains.messaging.models import MessageTemplate
        from apps.core.models import Tenant

        try:
            tenant = Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            raise CommandError(f"tenant_id={tenant_id} not found")

        self.stdout.write(f"대상 테넌트: {tenant.name} (id={tenant.id})")

        # 1. 학생 — name 또는 ps_number
        students = [
            s for s in Student.objects.filter(tenant_id=tenant_id)
            if matches_residue(s.name or "") or matches_residue(s.ps_number or "")
        ]

        # 2. 커뮤니티 게시글 — title
        posts = [
            p for p in PostEntity.objects.filter(tenant_id=tenant_id)
            if matches_residue(p.title or "")
        ]

        # 3. 매치업 문서 — title
        matchups = [
            m for m in MatchupDocument.objects.filter(tenant_id=tenant_id)
            if matches_residue(m.title or "")
        ]

        # 4. 메시지 템플릿 — name (시스템 템플릿 제외)
        templates = [
            t for t in MessageTemplate.objects.filter(tenant_id=tenant_id, is_system=False)
            if matches_residue(t.name or "")
        ]

        total = len(students) + len(posts) + len(matchups) + len(templates)

        self._print_group("학생 (Student)", students, limit, lambda s: f"id={s.id} ps={s.ps_number} name={s.name!r}")
        self._print_group("게시글 (PostEntity)", posts, limit, lambda p: f"id={p.id} title={p.title!r}")
        self._print_group("매치업 문서 (MatchupDocument)", matchups, limit, lambda m: f"id={m.id} title={m.title!r}")
        self._print_group("메시지 템플릿 (MessageTemplate)", templates, limit, lambda t: f"id={t.id} name={t.name!r}")

        self.stdout.write(self.style.HTTP_INFO(f"\n=== 합계: {total}건 ==="))

        if not execute:
            self.stdout.write(self.style.WARNING(
                "--dry-run 모드 (기본값). 실제 삭제하지 않음.\n"
                "삭제하려면 다시 실행 시 --execute 추가."
            ))
            return

        if total == 0:
            self.stdout.write("삭제할 잔재 없음 — 종료.")
            return

        # 삭제 실행
        with transaction.atomic():
            s_del = sum(s.delete()[0] for s in students)
            p_del = sum(p.delete()[0] for p in posts)
            m_del = sum(m.delete()[0] for m in matchups)
            t_del = sum(t.delete()[0] for t in templates)

        self.stdout.write(self.style.SUCCESS(
            f"\n삭제 완료:\n"
            f"  - 학생 cascade rows: {s_del}\n"
            f"  - 게시글 cascade rows: {p_del}\n"
            f"  - 매치업 cascade rows: {m_del}\n"
            f"  - 템플릿 cascade rows: {t_del}"
        ))

    def _print_group(self, label: str, items, limit: int, fmt):
        self.stdout.write(f"\n--- {label}: {len(items)}건 ---")
        for it in items[:limit]:
            self.stdout.write(f"  {fmt(it)}")
        if len(items) > limit:
            self.stdout.write(f"  ... 외 {len(items) - limit}건")
