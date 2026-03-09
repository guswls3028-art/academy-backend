# PATH: apps/domains/students/management/commands/check_password_reset_data.py
"""
스펙 변경(비밀번호 재설정: 학생/학부모 이름+번호 조회 → 임시 비밀번호 발송) 전에 생성된
잘못된 데이터 여부를 실제 DB 조회로 검사합니다.

검사 항목:
1. 활성 학생 중 학생 비밀번호 찾기 불가: user 없음, name/ps_number 비어 있음, 발송 가능 번호 없음
2. 활성 학생 중 학부모 비밀번호 찾기 불가: parent_phone 형식 오류, 해당 Parent 없음, Parent.user 없음
3. (tenant, name, ps_number) 중복 활성 학생
4. 전화번호 미정규화: 010 11자리 아님 또는 하이픈/공백 포함

사용:
  python manage.py check_password_reset_data
  python manage.py check_password_reset_data --verbose  # 건별 ID 출력
"""
import re
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from apps.domains.students.models import Student
from apps.domains.parents.models import Parent


def _normalize_phone(value):
    if not value:
        return ""
    s = str(value).replace(" ", "").replace("-", "").replace(".", "").strip()
    return s if len(s) == 11 and s.startswith("010") else ""


class Command(BaseCommand):
    help = "비밀번호 재설정 스펙 기준 잘못된 데이터 여부를 DB 조회로 검사"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="문제 건별로 ID 등 상세 출력",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]
        errors = []
        warnings = []

        # 활성 학생만 (삭제되지 않은 것)
        active_students = Student.objects.filter(deleted_at__isnull=True).select_related("user")

        # 1) 학생 비밀번호 찾기 불가: user 없음, name/ps_number 비어 있음, 발송 가능 번호 없음
        for s in active_students:
            if not s.user_id:
                errors.append(("student_no_user", s.id, s.tenant_id, f"student_id={s.id} user_id 없음 (학생 비밀번호 찾기 불가)"))
                continue
            if not (s.name or "").strip():
                errors.append(("student_empty_name", s.id, s.tenant_id, f"student_id={s.id} name 비어 있음"))
            if not (s.ps_number or "").strip():
                errors.append(("student_empty_ps_number", s.id, s.tenant_id, f"student_id={s.id} ps_number 비어 있음"))
            phone_norm = _normalize_phone(s.phone)
            parent_phone_norm = _normalize_phone(s.parent_phone)
            if not phone_norm and not parent_phone_norm:
                errors.append((
                    "student_no_send_phone",
                    s.id,
                    s.tenant_id,
                    f"student_id={s.id} (name={s.name!r}) 휴대번호·학부모번호 둘 다 없거나 010 11자리 아님 → 발송 불가",
                ))

        # 2) 학부모 비밀번호 찾기: parent_phone 정규화 검사 + 해당 Parent 존재 및 user 존재
        for s in active_students:
            if not (s.parent_phone or "").strip():
                continue
            pnorm = _normalize_phone(s.parent_phone)
            if not pnorm:
                warnings.append((
                    "parent_phone_not_01011",
                    s.id,
                    s.tenant_id,
                    f"student_id={s.id} parent_phone={s.parent_phone!r} (010 11자리 아님)",
                ))
                continue
            parent = Parent.objects.filter(tenant_id=s.tenant_id, phone=pnorm).first()
            if not parent:
                errors.append((
                    "parent_missing",
                    s.id,
                    s.tenant_id,
                    f"student_id={s.id} (name={s.name!r}) parent_phone={pnorm} 에 해당 Parent 없음",
                ))
            elif not parent.user_id:
                errors.append((
                    "parent_no_user",
                    parent.id,
                    s.tenant_id,
                    f"Parent id={parent.id} (phone={parent.phone}) user_id 없음 → 학부모 비밀번호 찾기 불가",
                ))

        # 3) (tenant, name, ps_number) 중복 활성 학생 — ps_number는 tenant당 유니크라서 0건일 수 있음
        dup = (
            Student.objects.filter(deleted_at__isnull=True)
            .values("tenant_id", "name", "ps_number")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        for d in dup:
            errors.append((
                "duplicate_name_ps",
                None,
                d["tenant_id"],
                f"tenant={d['tenant_id']} name={d['name']!r} ps_number={d['ps_number']!r} 중복 cnt={d['cnt']}",
            ))

        # 4) 전화번호 미정규화 (DB에 하이픈/공백 저장된 경우)
        for s in active_students:
            for field, label in [(s.phone, "phone"), (s.parent_phone, "parent_phone")]:
                if not (field or "").strip():
                    continue
                if re.search(r"[\s\-\.]", str(field)):
                    warnings.append((
                        "phone_not_normalized",
                        s.id,
                        s.tenant_id,
                        f"student_id={s.id} {label}={field!r} (하이픈/공백 포함 → 정규화 권장)",
                    ))

        # 결과 출력
        if errors:
            self.stdout.write(self.style.ERROR(f"\n[오류] {len(errors)}건 (비밀번호 재설정 스펙 위반)"))
            for code, pk, tenant_id, msg in errors[:50]:
                if verbose and pk is not None:
                    self.stdout.write(f"  [{code}] pk={pk} tenant_id={tenant_id} {msg}")
                else:
                    self.stdout.write(f"  {msg}")
            if len(errors) > 50:
                self.stdout.write(self.style.WARNING(f"  ... 외 {len(errors) - 50}건 (--verbose 로 전체 확인)"))
        else:
            self.stdout.write(self.style.SUCCESS("[오류] 0건"))

        if warnings:
            self.stdout.write(self.style.WARNING(f"\n[경고] {len(warnings)}건 (동작은 할 수 있으나 정리 권장)"))
            for code, pk, tenant_id, msg in warnings[:30]:
                if verbose and pk is not None:
                    self.stdout.write(f"  [{code}] pk={pk} tenant_id={tenant_id} {msg}")
                else:
                    self.stdout.write(f"  {msg}")
            if len(warnings) > 30:
                self.stdout.write(f"  ... 외 {len(warnings) - 30}건")
        else:
            self.stdout.write(self.style.SUCCESS("[경고] 0건"))

        if not errors and not warnings:
            self.stdout.write(self.style.SUCCESS("\n비밀번호 재설정 스펙 기준 잘못된 데이터 없음."))
        elif errors:
            self.stdout.write(self.style.ERROR(f"\n총 오류 {len(errors)}건 — 조치 후 다시 검사하세요."))
