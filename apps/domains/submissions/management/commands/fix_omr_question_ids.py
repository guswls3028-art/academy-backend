# apps/domains/submissions/management/commands/fix_omr_question_ids.py
"""
OMR 자동채점 버그 수정: SubmissionAnswer.exam_question_id가 문항 번호(1,2,3)로
잘못 저장된 레코드를 ExamQuestion PK로 교정하고 재채점한다.

사용:
  python manage.py fix_omr_question_ids --dry-run
  python manage.py fix_omr_question_ids
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "OMR SubmissionAnswer의 exam_question_id(번호→PK) 교정 + 재채점"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="변경 없이 진단만")
        parser.add_argument("--limit", type=int, default=500, help="최대 처리 건수")

    def handle(self, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        from apps.domains.submissions.models import Submission, SubmissionAnswer
        from apps.domains.exams.models import Exam, Sheet, ExamQuestion
        from apps.domains.exams.services.template_resolver import resolve_template_exam

        # OMR_SCAN 소스의 모든 submission (ANSWERS_READY, GRADING, DONE 포함)
        omr_subs = (
            Submission.objects
            .filter(source=Submission.Source.OMR_SCAN, target_type="exam")
            .exclude(status__in=["submitted", "failed"])
            .order_by("-created_at")[:limit]
        )

        self.stdout.write(f"OMR submissions found: {len(omr_subs)}")

        fixed_subs = 0
        fixed_answers = 0
        regraded = 0
        errors = 0

        for sub in omr_subs:
            try:
                answers = list(SubmissionAnswer.objects.filter(submission=sub))
                if not answers:
                    continue

                # Sheet/ExamQuestion 조회
                exam = Exam.objects.filter(id=int(sub.target_id)).first()
                if not exam:
                    continue

                template_exam = resolve_template_exam(exam)
                sheet = Sheet.objects.filter(exam=template_exam).first()
                if not sheet:
                    continue

                questions = list(ExamQuestion.objects.filter(sheet=sheet).only("id", "number"))
                if not questions:
                    continue

                qnum_to_pk = {int(q.number): int(q.id) for q in questions}
                pk_set = set(qnum_to_pk.values())

                # 이미 PK인지 번호인지 판단:
                # answer의 exam_question_id가 전부 pk_set에 있으면 이미 정상
                current_ids = {int(a.exam_question_id) for a in answers}
                if current_ids.issubset(pk_set):
                    continue  # 이미 정상

                # question_number 범위인지 확인 (1~N)
                qnum_set = set(qnum_to_pk.keys())
                if not current_ids.issubset(qnum_set):
                    # 번호도 PK도 아닌 이상한 상태 — 스킵
                    self.stdout.write(
                        f"  [SKIP] sub={sub.id} ids={current_ids} "
                        f"not in numbers={qnum_set} nor pks={pk_set}"
                    )
                    continue

                # 번호→PK 교정
                self.stdout.write(
                    f"  [FIX] sub={sub.id} tenant={sub.tenant_id} "
                    f"answers={len(answers)} status={sub.status}"
                )

                if dry_run:
                    fixed_subs += 1
                    fixed_answers += len(answers)
                    continue

                with transaction.atomic():
                    for a in answers:
                        old_id = int(a.exam_question_id)
                        new_id = qnum_to_pk.get(old_id)
                        if new_id and new_id != old_id:
                            # unique_together (submission, exam_question_id) 충돌 방지:
                            # 같은 submission에 new_id가 이미 있으면 삭제
                            SubmissionAnswer.objects.filter(
                                submission=sub, exam_question_id=new_id,
                            ).exclude(pk=a.pk).delete()

                            a.exam_question_id = new_id
                            a.save(update_fields=["exam_question_id", "updated_at"])
                            fixed_answers += 1

                    fixed_subs += 1

                # 재채점 (ANSWERS_READY 이후 상태만)
                if sub.status in ("answers_ready", "grading", "done"):
                    try:
                        from apps.domains.results.services.grading_service import grade_submission
                        grade_submission(int(sub.id))
                        regraded += 1
                        self.stdout.write(f"    [REGRADE] sub={sub.id} OK")
                    except Exception as e:
                        self.stdout.write(f"    [REGRADE_ERR] sub={sub.id}: {e}")
                        errors += 1

            except Exception as exc:
                errors += 1
                self.stdout.write(f"  [ERROR] sub={sub.id}: {exc}")
                logger.exception("fix_omr_question_ids error sub=%s", sub.id)

        summary = (
            f"DONE | dry_run={dry_run} | "
            f"fixed_subs={fixed_subs} | fixed_answers={fixed_answers} | "
            f"regraded={regraded} | errors={errors}"
        )
        self.stdout.write(summary)
        logger.info("fix_omr_question_ids: %s", summary)
