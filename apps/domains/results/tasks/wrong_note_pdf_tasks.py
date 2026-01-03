# apps/domains/results/tasks/wrong_note_pdf_tasks.py
from celery import shared_task

from apps.domains.results.models.wrong_note_pdf import WrongNotePDF
from worker.wrong_notes.generator import generate_wrong_note_pdf


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
)
def generate_wrong_note_pdf_task(self, job_id: int) -> bool:
    """
    ❗ 계약 역할만 수행
    ❗ 실제 계산/생성은 worker로 위임
    """
    job = WrongNotePDF.objects.get(id=job_id)

    job.status = WrongNotePDF.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        file_path = generate_wrong_note_pdf(job_id)
        job.file_path = file_path
        job.status = WrongNotePDF.Status.DONE
        job.save(update_fields=["status", "file_path"])
        return True

    except Exception as e:
        job.status = WrongNotePDF.Status.FAILED
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message"])
        raise
