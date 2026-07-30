# PATH: apps/core/management/commands/cleanup_e2e_residue.py
"""
E2E 테스트 잔재 데이터 정리 커맨드.

배경:
    운영 테넌트(Tenant 1 / hakwonplus)에 E2E 자동화 스펙이 생성한 학생·게시글·
    메시지 템플릿·매치업 파일·수납 비목이 누적되어 학원 운영 화면의 품질을 저하시킴.

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
    python manage.py cleanup_e2e_residue --tenant-id 1 --execute --confirm-token <dry-run token>
"""
import hashlib
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# 명백한 E2E 지문 — 자연어와 겹치지 않는 식별자 패턴만 허용
RESIDUE_PATTERNS = [
    re.compile(r"\[E2E-\d{6,}"),
    re.compile(r"\[E2E\] "),                 # 2026-05-02: 운영 잔재 [E2E] 공지... 형식
    re.compile(r"\[AUDIT-\w*-?\d{6,}"),
    re.compile(r"\[CHAOS-\d{3,}"),
    re.compile(r"^E2E-\d{6,}"),
    re.compile(r"AUDIT-CRUD-\d{6,}"),
    re.compile(r"^EDITED-\d{5,}$"),
    # 괄호 없는 타임스탬프 접두 패턴 — 자연어에 나타날 수 없음
    re.compile(r"^E2E학생\d{6,}"),
    re.compile(r"^E2E\d{6,}"),
    re.compile(r"^E2E메시지\d+"),             # 2026-05-02: 송장에 노출된 E2E 발신 학생명
    re.compile(r"^E2E ?Test ?Exam \d{6,}"),  # 2026-05-02: 운영 시험 도배 패턴
    re.compile(r"^E2E시험"),
    re.compile(r"^두번째 시험임$"),           # 2026-05-02: 명시적 테스트 명칭
    re.compile(r"^CHAOS-\d{6,}"),
]

def matches_residue(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in RESIDUE_PATTERNS)


def matches_template_residue(text: str) -> bool:
    return matches_residue(text)


def build_confirmation_token(*, tenant_id: int, target_groups: dict[str, list]) -> str:
    """Bind an execute attempt to the exact dry-run target set."""
    target_parts = [f"tenant:{tenant_id}"]
    for label in sorted(target_groups):
        ids = sorted(int(item.id) for item in target_groups[label])
        target_parts.append(f"{label}:{','.join(str(item_id) for item_id in ids)}")
    return hashlib.sha256("|".join(target_parts).encode("utf-8")).hexdigest()


class Command(BaseCommand):
    help = "Tenant별 E2E 자동화 잔재 데이터(학생·게시글·메시지 템플릿·매치업 문서·수납 비목)를 식별/삭제한다."

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
            help="실제 삭제 실행. 직전 dry-run의 --confirm-token이 함께 필요.",
        )
        parser.add_argument(
            "--confirm-token",
            default="",
            help="직전 dry-run이 출력한 exact-target 확인 토큰.",
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
        confirm_token: str = (options["confirm_token"] or "").strip()

        # 모델 지연 import (apps 레지스트리 완료 후)
        from apps.domains.students.models import Student
        from apps.domains.community.models.post import PostEntity
        from apps.domains.matchup.models import MatchupDocument
        from apps.domains.messaging.models import MessageTemplate
        from apps.domains.exams.models.exam import Exam
        from apps.domains.homework_results.models.homework import Homework
        from apps.domains.fees.models import FeeTemplate, InvoiceItem
        from apps.domains.progress.models import ClinicLink
        from apps.domains.results.models import Result
        from apps.domains.submissions.models import Submission
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
            if matches_template_residue(t.name or "")
        ]

        # 5. 시험 — title (template/regular 모두). 2026-05-02 운영 [E2E Test Exam ...] 도배 sweep.
        exams = [
            e for e in Exam.objects.filter(tenant_id=tenant_id)
            if matches_residue(e.title or "")
        ]

        # 6. 과제 — title. 동일 패턴.
        homeworks = [
            h for h in Homework.objects.filter(tenant_id=tenant_id)
            if matches_residue(h.title or "")
        ]

        # 7. 수납 비목 — name. 연결된 학생비용/청구항목이 있으면 삭제 대신 비활성화한다.
        fee_templates = [
            f for f in FeeTemplate.objects.filter(tenant_id=tenant_id)
            if matches_residue(f.name or "")
        ]

        def fee_template_ref_count(fee_template) -> int:
            return (
                fee_template.student_fees.count()
                + InvoiceItem.objects.filter(fee_template=fee_template).count()
            )

        def fee_template_action(fee_template) -> str:
            return "deactivate" if fee_template_ref_count(fee_template) else "delete"

        total = (
            len(students) + len(posts) + len(matchups) + len(templates)
            + len(exams) + len(homeworks) + len(fee_templates)
        )

        self._print_group(
            "학생 (Student)",
            students,
            limit,
            lambda s: (
                f"id={s.id} ps={s.ps_number} name={s.name!r} "
                f"deleted={s.deleted_at is not None} user_id={s.user_id}"
            ),
        )
        self._print_group("게시글 (PostEntity)", posts, limit, lambda p: f"id={p.id} title={p.title!r}")
        self._print_group(
            "매치업 문서 (MatchupDocument)",
            matchups,
            limit,
            lambda m: (
                f"id={m.id} inventory_id={m.inventory_file_id} title={m.title!r} "
                f"problems={m.problems.count()} reports={m.hit_reports.count()}"
            ),
        )
        self._print_group(
            "메시지 템플릿 (MessageTemplate)",
            templates,
            limit,
            lambda t: (
                f"id={t.id} name={t.name!r} default={t.is_user_default} "
                f"autosend_refs={t.auto_send_configs.count()}"
            ),
        )
        self._print_group("시험 (Exam)", exams, limit, lambda e: f"id={e.id} type={e.exam_type} title={e.title!r}")
        self._print_group("과제 (Homework)", homeworks, limit, lambda h: f"id={h.id} title={h.title!r}")
        self._print_group(
            "수납 비목 (FeeTemplate)",
            fee_templates,
            limit,
            lambda f: (
                f"id={f.id} name={f.name!r} active={f.is_active} "
                f"refs={fee_template_ref_count(f)} action={fee_template_action(f)}"
            ),
        )

        target_groups = {
            "exams": exams,
            "fee_templates": fee_templates,
            "homeworks": homeworks,
            "matchups": matchups,
            "posts": posts,
            "students": students,
            "templates": templates,
        }
        exact_token = build_confirmation_token(
            tenant_id=tenant_id,
            target_groups=target_groups,
        )

        self.stdout.write(self.style.HTTP_INFO(f"\n=== 합계: {total}건 ==="))
        self.stdout.write(f"확인 토큰 (exact targets): {exact_token}")

        if not execute:
            self.stdout.write(self.style.WARNING(
                "--dry-run 모드 (기본값). 실제 삭제하지 않음.\n"
                "삭제하려면 동일 대상에 대해 --execute --confirm-token <위 토큰>을 사용."
            ))
            return

        if total == 0:
            self.stdout.write("삭제할 잔재 없음 — 종료.")
            return

        if confirm_token != exact_token:
            raise CommandError(
                "확인 토큰이 없거나 현재 exact target set과 일치하지 않습니다. "
                "dry-run을 다시 실행해 대상 ID를 검토하세요."
            )

        self._validate_execute_targets(
            students=students,
            matchups=matchups,
            templates=templates,
        )
        storage_deleted = self._delete_external_storage(
            tenant_id=tenant_id,
            posts=posts,
            matchups=matchups,
        )

        # 외부 저장소 삭제가 모두 검증된 뒤 DB를 한 트랜잭션으로 정리한다.
        with transaction.atomic():
            from apps.domains.students.services.lifecycle import permanently_delete_students

            student_result = permanently_delete_students(
                tenant=tenant,
                student_ids=[student.id for student in students],
            )
            s_del = student_result.deleted_count
            p_del = sum(p.delete()[0] for p in posts)
            m_del = sum(m.inventory_file.delete()[0] for m in matchups)
            t_del = sum(t.delete()[0] for t in templates)
            exam_ids = [exam.id for exam in exams]
            result_del = (
                Result.objects.filter(target_type="exam", target_id__in=exam_ids).delete()[0]
                if exam_ids else 0
            )
            submission_del = (
                Submission.objects.filter(
                    tenant_id=tenant_id,
                    target_type=Submission.TargetType.EXAM,
                    target_id__in=exam_ids,
                ).delete()[0]
                if exam_ids else 0
            )
            clinic_link_del = (
                ClinicLink.objects.filter(
                    tenant_id=tenant_id,
                    source_type="exam",
                    source_id__in=exam_ids,
                ).delete()[0]
                if exam_ids else 0
            )
            e_del = sum(e.delete()[0] for e in exams)
            h_del = sum(h.delete()[0] for h in homeworks)
            f_del = 0
            f_deactivated = 0
            for fee_template in fee_templates:
                if fee_template_ref_count(fee_template):
                    if fee_template.is_active or fee_template.auto_assign:
                        fee_template.is_active = False
                        fee_template.auto_assign = False
                        fee_template.save(update_fields=["is_active", "auto_assign", "updated_at"])
                        f_deactivated += 1
                else:
                    f_del += fee_template.delete()[0]

        self.stdout.write(self.style.SUCCESS(
            f"\n삭제 완료:\n"
            f"  - 학생 cascade rows: {s_del}\n"
            f"  - 게시글 cascade rows: {p_del}\n"
            f"  - 매치업/인벤토리 cascade rows: {m_del}\n"
            f"  - R2 objects verified deleted: {storage_deleted}\n"
            f"  - 템플릿 cascade rows: {t_del}\n"
            f"  - 시험 결과 cascade rows: {result_del}\n"
            f"  - 시험 제출 cascade rows: {submission_del}\n"
            f"  - 시험 클리닉 링크 cascade rows: {clinic_link_del}\n"
            f"  - 시험 cascade rows: {e_del}\n"
            f"  - 과제 cascade rows: {h_del}\n"
            f"  - 수납 비목 cascade rows: {f_del}\n"
            f"  - 수납 비목 비활성화: {f_deactivated}"
        ))

    @staticmethod
    def _validate_execute_targets(*, students, matchups, templates) -> None:
        active_student_ids = [student.id for student in students if student.deleted_at is None]
        if active_student_ids:
            raise CommandError(
                "활성 학생은 E2E 표식만으로 영구 삭제하지 않습니다. "
                f"먼저 공식 soft-delete 수명주기를 완료하세요: ids={active_student_ids}"
            )

        referenced_template_ids = [
            template.id
            for template in templates
            if template.is_user_default or template.auto_send_configs.exists()
        ]
        if referenced_template_ids:
            raise CommandError(
                "기본/자동발송 참조 템플릿은 자동 정리하지 않습니다: "
                f"ids={referenced_template_ids}"
            )

        from apps.domains.matchup.services import document_has_protected_matchup_problems

        protected_document_ids = []
        for document in matchups:
            has_authored_report = document.hit_reports.exclude(
                status="draft",
                submitted_at__isnull=True,
                summary="",
                entries__isnull=True,
            ).exists()
            if (
                document_has_protected_matchup_problems(document)
                or has_authored_report
            ):
                protected_document_ids.append(document.id)
        if protected_document_ids:
            raise CommandError(
                "수동 컷/핀 또는 작성된 보고서가 있는 매치업 문서는 자동 정리하지 않습니다: "
                f"ids={protected_document_ids}"
            )

    @staticmethod
    def _delete_external_storage(*, tenant_id: int, posts, matchups) -> int:
        from apps.domains.community.models import PostAttachment
        from apps.domains.matchup.models import ProblemSegmentationProposal
        from apps.infrastructure.storage.r2 import (
            delete_object_r2_storage,
            head_object_r2_storage,
        )

        keys: list[str] = list(
            PostAttachment.objects.filter(post__in=posts)
            .exclude(r2_key="")
            .values_list("r2_key", flat=True)
        )
        for document in matchups:
            if document.r2_key:
                keys.append(document.r2_key)
            if document.inventory_file.r2_key:
                keys.append(document.inventory_file.r2_key)
            for image_key, meta in document.problems.values_list("image_key", "meta"):
                if image_key:
                    keys.append(image_key)
                cleanup = (meta or {}).get("public_cleanup")
                public_key = cleanup.get("public_image_key") if isinstance(cleanup, dict) else ""
                if public_key:
                    keys.append(public_key)
            keys.extend(
                key
                for key in ((document.meta or {}).get("page_image_keys") or [])
                if isinstance(key, str) and key
            )
            keys.extend(
                ProblemSegmentationProposal.objects.filter(document=document)
                .exclude(image_key="")
                .values_list("image_key", flat=True)
            )

        unique_keys = list(dict.fromkeys(keys))
        wrong_tenant_keys = [
            key for key in unique_keys
            if not key.startswith(f"tenants/{tenant_id}/")
        ]
        if wrong_tenant_keys:
            raise CommandError(
                "테넌트 prefix 밖의 R2 key가 포함되어 저장소 정리를 중단합니다. "
                f"count={len(wrong_tenant_keys)}"
            )

        for key in unique_keys:
            delete_object_r2_storage(key=key)
            exists, _size = head_object_r2_storage(key=key)
            if exists:
                raise CommandError("R2 삭제 readback 실패. DB 정리를 중단합니다.")
        return len(unique_keys)

    def _print_group(self, label: str, items, limit: int, fmt):
        self.stdout.write(f"\n--- {label}: {len(items)}건 ---")
        for it in items[:limit]:
            self.stdout.write(f"  {fmt(it)}")
        if len(items) > limit:
            self.stdout.write(f"  ... 외 {len(items) - limit}건")
