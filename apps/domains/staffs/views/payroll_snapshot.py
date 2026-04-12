# PATH: apps/domains/staffs/views/payroll_snapshot.py

from io import BytesIO

from django.http import HttpResponse

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ReadOnlyModelViewSet

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.lib.styles import getSampleStyleSheet

from ..serializers import PayrollSnapshotSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager

# ===========================
# PayrollSnapshot (ReadOnly + Export)
# ===========================

class PayrollSnapshotViewSet(ReadOnlyModelViewSet):
    serializer_class = PayrollSnapshotSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def get_queryset(self):
        return staff_repo.payroll_snapshot_queryset_tenant(self.request.tenant)

    @action(detail=False, methods=["post"], url_path="export-excel")
    def export_excel(self, request):
        """급여 엑셀 내보내기(워커 비동기). POST body: { "year", "month" } → job_id 반환. GET /api/v1/jobs/<job_id>/ 폴링 후 result.download_url 로 다운로드."""
        year = request.data.get("year") or request.query_params.get("year")
        month = request.data.get("month") or request.query_params.get("month")

        if not year or not month:
            raise ValidationError("year, month는 필수입니다.")

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.domains.ai.gateway import dispatch_job

        out = dispatch_job(
            job_type="staff_excel_export",
            payload={
                "tenant_id": str(tenant.id),
                "year": int(year),
                "month": int(month),
            },
            tenant_id=str(tenant.id),
            source_domain="staffs",
            source_id=f"{year}-{month}",
            tier="basic",
            idempotency_key=f"staff_export:{tenant.id}:{year}:{month}",
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

        if not staff_id or not year or not month:
            raise ValidationError("staff, year, month는 필수입니다.")

        snap = self.get_queryset().filter(
            staff_id=staff_id,
            year=year,
            month=month,
        ).first()

        if not snap:
            return Response({"detail": "급여 스냅샷 없음"}, status=404)

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("급여 명세서", styles["Title"]))
        story.append(Spacer(1, 12))

        meta = [
            ["직원명", snap.staff.name],
            ["정산월", f"{snap.year}-{snap.month:02d}"],
            ["확정자", getattr(snap.generated_by, "username", "-") if snap.generated_by else "-"],
        ]
        story.append(Table(meta, colWidths=[120, 360]))
        story.append(Spacer(1, 16))

        rows = [
            ["근무시간", f"{snap.work_hours} h"],
            ["급여", f"{snap.work_amount:,} 원"],
            ["승인 비용", f"{snap.approved_expense_amount:,} 원"],
            ["총 지급액", f"{snap.total_amount:,} 원"],
        ]
        story.append(Table(rows, colWidths=[120, 360]))

        doc.build(story)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{snap.staff.id}_{snap.year}_{snap.month:02d}.pdf"'
        )
        response.write(pdf)
        return response
