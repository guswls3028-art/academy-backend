# apps/domains/results/tasks/wrong_note_pdf_tasks.py
from celery import shared_task
from apps.domains.results.models.wrong_note_pdf import WrongNotePDF


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
)
def generate_wrong_note_pdf_task(self, job_id: int) -> bool:
    """
    🔴 UX/운영 패치
    - API 서버는 RUNNING까지만 책임
    - DONE은 외부 worker만 찍음
    """

    job = WrongNotePDF.objects.get(id=job_id)

    job.status = WrongNotePDF.Status.RUNNING
    job.save(update_fields=["status"])

    # ------------------------------------------------
    # ❗ 실제 PDF 생성은 외부 Worker 책임
    # 여기서는 enqueue만 수행
    # ------------------------------------------------
    # enqueue_wrong_note_pdf_job(job_id)
    # TODO: worker 연동

    return True
