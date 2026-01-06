# apps/domains/results/tasks/wrong_note_pdf_tasks.py
from celery import shared_task

from apps.domains.results.models.wrong_note_pdf import WrongNotePDF

# ❌ worker 코드는 API 서버에서 import 금지
# 실제 PDF 생성은 "외부 worker"가 처리해야 함


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
)
def generate_wrong_note_pdf_task(self, job_id: int) -> bool:
    """
    ❗ API 서버 역할
    - Job 상태만 관리
    - 실제 PDF 생성은 외부 Worker 책임

    이 Task는 '트리거 역할'만 수행함
    """

    job = WrongNotePDF.objects.get(id=job_id)

    # 1️⃣ 상태 변경
    job.status = WrongNotePDF.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        # ------------------------------------------------
        # ✅ 실제 PDF 생성은 여기서 하지 않음
        # ------------------------------------------------
        # - Redis / Queue / HTTP 등을 통해
        # - Worker에게 job_id 전달만 함
        #
        # 예:
        # enqueue_wrong_note_pdf_job(job_id)
        #
        # 지금은 구조만 맞추고 PASS
        # ------------------------------------------------

        # 🔧 임시 처리 (Worker 연동 전까지)
        job.status = WrongNotePDF.Status.DONE
        job.file_path = ""  # Worker가 채울 예정
        job.save(update_fields=["status", "file_path"])

        return True

    except Exception as e:
        job.status = WrongNotePDF.Status.FAILED
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message"])
        raise
