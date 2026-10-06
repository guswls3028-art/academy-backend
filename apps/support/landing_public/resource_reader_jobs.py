"""The public landing domain's narrow Tools queue dependency boundary."""


def create_reader_job(*, file_id, tenant_id, token):
    from apps.domains.ai.models import AIJobModel

    return AIJobModel.objects.create(
        job_id=f"resource-reader-{token}", job_type="public_resource_reader",
        status="PENDING", tenant_id=str(tenant_id), source_domain="landing_public_resource",
        source_id=file_id, payload={"token": token}, tier="basic",
    )


def publish_reader_job(job):
    from apps.domains.ai.queueing.publisher import publish_ai_job_sqs

    return publish_ai_job_sqs(job)


def fail_reader_job(job):
    from django.utils import timezone
    from apps.domains.ai.models import AIJobModel

    # Do not leave a known queue failure looking like live pending work.
    AIJobModel.objects.filter(pk=job.pk, status="PENDING").update(
        status="FAILED", completed_at=timezone.now(),
        error_message="Public reader queue unavailable", last_error="Public reader queue unavailable",
    )
