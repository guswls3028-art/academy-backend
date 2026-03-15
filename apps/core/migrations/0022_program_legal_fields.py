# Generated additive migration — legal info fields on Program
# Safe for zero-downtime: all new columns with defaults, no NOT NULL without default.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_program_billing_mode_program_cancel_at_period_end_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="legal_company_name",
            field=models.CharField(blank=True, default="", help_text="상호 (법적 고지용)", max_length=200),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_representative",
            field=models.CharField(blank=True, default="", help_text="대표자명", max_length=100),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_business_number",
            field=models.CharField(blank=True, default="", help_text="사업자등록번호", max_length=50),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_ecommerce_number",
            field=models.CharField(blank=True, default="", help_text="통신판매업 신고번호", max_length=100),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_address",
            field=models.CharField(blank=True, default="", help_text="사업장 주소", max_length=500),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_support_email",
            field=models.CharField(blank=True, default="", help_text="고객센터 이메일", max_length=200),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_support_phone",
            field=models.CharField(blank=True, default="", help_text="고객센터 전화번호", max_length=50),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_privacy_officer_name",
            field=models.CharField(blank=True, default="", help_text="개인정보 보호책임자 성명", max_length=100),
        ),
        migrations.AddField(
            model_name="program",
            name="legal_privacy_officer_contact",
            field=models.CharField(blank=True, default="", help_text="개인정보 보호책임자 연락처 (전화 또는 이메일)", max_length=200),
        ),
    ]
