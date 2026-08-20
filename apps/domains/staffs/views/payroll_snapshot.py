# PATH: apps/domains/staffs/views/payroll_snapshot.py

import hashlib
import os
from io import BytesIO

from django.http import HttpResponse

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ReadOnlyModelViewSet

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..serializers import PayrollSnapshotSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager, StaffDomainPagination
from apps.core.models import TenantMembership
from apps.core.parsing import parse_bool
from apps.support.staffs.ai_dependencies import dispatch_staffs_ai_job

# ===========================
# PayrollSnapshot (ReadOnly + Export)
# ===========================

def _parse_year_month(year, month):
    try:
        parsed_year = int(year)
        parsed_month = int(month)
    except (TypeError, ValueError) as exc:
        raise ValidationError("year, month는 정수여야 합니다.") from exc
    if not 2020 <= parsed_year <= 2100:
        raise ValidationError("year는 2020~2100 사이여야 합니다.")
    if not 1 <= parsed_month <= 12:
        raise ValidationError("month는 1~12 사이여야 합니다.")
    return parsed_year, parsed_month


def _ensure_korean_font():
    regular_name = "StaffPayrollRegular"
    bold_name = "StaffPayrollBold"
    try:
        pdfmetrics.getFont(regular_name)
        pdfmetrics.getFont(bold_name)
        return regular_name, bold_name
    except Exception:
        pass

    fonts_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "assets",
            "omr",
            "renderer",
            "fonts",
        )
    )
    candidates = {
        regular_name: [
            os.path.join(fonts_dir, "NotoSansKR-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        ],
        bold_name: [
            os.path.join(fonts_dir, "NotoSansKR-Bold.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
        ],
    }
    registered = {}
    for font_name, paths in candidates.items():
        registered[font_name] = False
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                registered[font_name] = True
                break
            except Exception:
                continue
    if not registered[regular_name]:
        return "Helvetica", "Helvetica-Bold"
    if not registered[bold_name]:
        return regular_name, regular_name
    return regular_name, bold_name


class PayrollSnapshotViewSet(ReadOnlyModelViewSet):
    serializer_class = PayrollSnapshotSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]
    pagination_class = StaffDomainPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["staff", "year", "month"]

    def get_queryset(self):
        return staff_repo.payroll_snapshot_queryset_tenant(self.request.tenant)

    @action(detail=False, methods=["post"], url_path="export-excel")
    def export_excel(self, request):
        """급여 엑셀 내보내기(워커 비동기). POST body: { "year", "month" } → job_id 반환. GET /api/v1/jobs/<job_id>/ 폴링 후 result.download_url 로 다운로드."""
        year = request.data.get("year") or request.query_params.get("year")
        month = request.data.get("month") or request.query_params.get("month")

        if year is None or month is None:
            raise ValidationError("year, month는 필수입니다.")
        year, month = _parse_year_month(year, month)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        owner_user_ids = TenantMembership.objects.filter(
            tenant=tenant,
            role="owner",
            is_active=True,
        ).values_list("user_id", flat=True)
        activity_staff_ids = staff_repo.payroll_activity_staff_ids(
            tenant,
            year,
            month,
        )
        expected_staff_ids = set(
            staff_repo.staff_queryset_tenant(tenant)
            .filter(id__in=activity_staff_ids)
            .exclude(user_id__in=owner_user_ids)
            .values_list("id", flat=True)
        )
        snapshot_ids_by_staff = dict(
            self.get_queryset()
            .filter(year=year, month=month)
            .values_list("staff_id", "id")
        )
        missing_staff_ids = sorted(
            expected_staff_ids - set(snapshot_ids_by_staff)
        )
        if missing_staff_ids:
            raise ValidationError(
                {
                    "detail": (
                        "해당 월에 기록이 있는 직원 전원의 월마감을 완료한 뒤 "
                        "정산 엑셀을 내보내 주세요."
                    ),
                    "unclosed_staff_ids": missing_staff_ids,
                }
            )
        if not snapshot_ids_by_staff:
            raise ValidationError("내보낼 정산 스냅샷이 없습니다.")

        snapshot_ids = sorted(snapshot_ids_by_staff.values())
        revision_source = ",".join(str(snapshot_id) for snapshot_id in snapshot_ids)
        revision = hashlib.sha256(
            revision_source.encode("utf-8")
        ).hexdigest()[:16]
        force_rerun = parse_bool(
            request.data.get("force_rerun", False),
            field_name="force_rerun",
        )
        out = dispatch_staffs_ai_job(
            job_type="staff_excel_export",
            payload={
                "tenant_id": str(tenant.id),
                "year": year,
                "month": month,
                "snapshot_ids": snapshot_ids,
                "revision": revision,
            },
            tenant_id=str(tenant.id),
            source_domain="staffs",
            source_id=f"{year}-{month}",
            tier="basic",
            idempotency_key=(
                f"staff_export:{tenant.id}:{year}:{month}:{revision}"
            ),
            force_rerun=force_rerun,
            rerun_reason="사용자 재시도" if force_rerun else "",
        )
        if not out.get("ok"):
            return Response(
                {"detail": out.get("error", "job 등록 실패")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"job_id": out["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request):
        staff_id = request.query_params.get("staff")
        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if staff_id is None or year is None or month is None:
            raise ValidationError("staff, year, month는 필수입니다.")
        try:
            staff_id = int(staff_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("staff는 정수여야 합니다.") from exc
        if staff_id < 1:
            raise ValidationError("staff는 양의 정수여야 합니다.")
        year, month = _parse_year_month(year, month)

        snap = self.get_queryset().filter(
            staff_id=staff_id,
            year=year,
            month=month,
        ).first()

        if not snap:
            return Response({"detail": "정산 스냅샷 없음"}, status=404)

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        regular_font, bold_font = _ensure_korean_font()
        styles["Title"].fontName = bold_font
        styles["Normal"].fontName = regular_font
        story = []

        story.append(Paragraph("근태·경비 정산 참고서", styles["Title"]))
        story.append(Spacer(1, 12))

        meta = [
            ["직원명", snap.staff_name or snap.staff.name],
            ["정산월", f"{snap.year}-{snap.month:02d}"],
            ["확정자", getattr(snap.generated_by, "username", "-") if snap.generated_by else "-"],
        ]
        meta_table = Table(meta, colWidths=[120, 360])
        meta_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), regular_font),
                    ("FONTNAME", (0, 0), (0, -1), bold_font),
                ]
            )
        )
        story.append(meta_table)
        story.append(Spacer(1, 16))

        rows = [
            ["근무시간", f"{snap.work_hours} h"],
            ["근무기록 금액", f"{snap.work_amount:,} 원"],
            ["승인 선결제 환급", f"{snap.approved_expense_amount:,} 원"],
            ["정산 합계(공제 전)", f"{snap.total_amount:,} 원"],
        ]
        rows_table = Table(rows, colWidths=[120, 360])
        rows_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), regular_font),
                    ("FONTNAME", (0, 0), (0, -1), bold_font),
                ]
            )
        )
        story.append(rows_table)
        story.append(Spacer(1, 12))
        story.append(
            Paragraph(
                "이 문서는 근태 기록과 직원 선결제 환급을 합산한 내부 참고자료입니다. "
                "법정 임금명세서가 아니며 세금·4대보험·연장·야간·휴일수당 등은 "
                "별도 확인이 필요합니다.",
                styles["Normal"],
            )
        )

        doc.build(story)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{snap.staff.id}_{snap.year}_{snap.month:02d}.pdf"'
        )
        response.write(pdf)
        return response
