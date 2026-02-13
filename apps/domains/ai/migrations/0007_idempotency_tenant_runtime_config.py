# Phase 0 안정성: Idempotency, TenantConfig, AIRuntimeConfig

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_domain', '0006_add_tier_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='aijobmodel',
            name='idempotency_key',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='tenant_id:exam_id:student_id:job_type:file_hash, 중복 요청 방지',
                max_length=256,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='aijobmodel',
            name='force_rerun',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='aijobmodel',
            name='rerun_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.CreateModel(
            name='TenantConfigModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('has_premium_subscription', models.BooleanField(default=False)),
                ('allow_gpu_fallback', models.BooleanField(default=False)),
                ('gpu_fallback_threshold', models.FloatField(default=0.5)),
            ],
            options={
                'db_table': 'ai_tenant_config',
            },
        ),
        migrations.CreateModel(
            name='AIRuntimeConfigModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(db_index=True, max_length=128, unique=True)),
                ('value', models.CharField(blank=True, max_length=512)),
            ],
            options={
                'db_table': 'ai_runtime_config',
            },
        ),
    ]
