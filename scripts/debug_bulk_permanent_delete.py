#!/usr/bin/env python
"""
bulk_permanent_delete 원인 추적: 단계별 실행 후 실패한 단계와 예외 출력.
사용법: python apps/api/manage.py shell < scripts/debug_bulk_permanent_delete.py
"""
import os
import sys
import django

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.base")
django.setup()

from django.db import connection

def run():
    from academy.adapters.db.django import repositories_students as student_repo
    from apps.core.models import Tenant

    tenant = Tenant.objects.first()
    if not tenant:
        print("No tenant found.")
        return
    from apps.domains.students.models import Student
    to_delete = list(
        Student.objects.filter(tenant=tenant, deleted_at__isnull=False)
        .select_related("user")[:10]
    )
    if not to_delete:
        print("No deleted students for tenant.", tenant.id)
        return
    student_ids = [s.id for s in to_delete]
    user_ids = [s.user_id for s in to_delete if s.user_id]
    print("tenant_id", tenant.id, "student_ids", student_ids, "user_ids", user_ids)

    sub = "SELECT id FROM enrollment_enrollment WHERE student_id IN %s"
    params = [tuple(student_ids)]

    steps = [
        ("results_result_item", f"result_id IN (SELECT id FROM results_result WHERE enrollment_id IN ({sub}))", params),
        ("results_result", f"enrollment_id IN ({sub})", params),
        ("results_exam_attempt", f"enrollment_id IN ({sub})", params),
        ("results_fact", f"enrollment_id IN ({sub})", params),
        ("results_wrong_note_pdf", f"enrollment_id IN ({sub})", params),
        ("results_exam_result", f"submission_id IN (SELECT id FROM submissions_submission WHERE enrollment_id IN ({sub}))", params),
        ("submissions_submissionanswer", f"submission_id IN (SELECT id FROM submissions_submission WHERE enrollment_id IN ({sub}))", params),
        ("submissions_submission", f"enrollment_id IN ({sub})", params),
        ("homework_results_homeworkscore", f"enrollment_id IN ({sub})", params),
        ("homework_assignment", f"enrollment_id IN ({sub})", params),
        ("homework_enrollment", f"enrollment_id IN ({sub})", params),
    ]
    enrollment_child = [
        "attendance_attendance", "enrollment_sessionenrollment",
        "video_videopermission", "video_videoprogress", "video_videoplaysession", "video_videoplaybackevent",
        "progress_sessionprogress", "progress_lectureprogress", "progress_cliniclink", "progress_risklog",
    ]

    try:
        with connection.cursor() as cursor:
            for tbl, where_sql, where_params in steps:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                    ["public", tbl],
                )
                if cursor.fetchone():
                    print("DELETE", tbl)
                    cursor.execute(f"DELETE FROM {tbl} WHERE {where_sql}", where_params)
            for tbl in enrollment_child:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                    ["public", tbl],
                )
                if cursor.fetchone():
                    print("DELETE", tbl)
                    cursor.execute(f"DELETE FROM {tbl} WHERE enrollment_id IN ({sub})", params)
            print("DELETE enrollment_enrollment")
            cursor.execute("DELETE FROM enrollment_enrollment WHERE student_id IN %s", [tuple(student_ids)])
            print("DELETE students_studenttag")
            cursor.execute("DELETE FROM students_studenttag WHERE student_id IN %s", [tuple(student_ids)])
            print("UPDATE students_studentregistrationrequest")
            cursor.execute(
                "UPDATE students_studentregistrationrequest SET student_id = NULL WHERE student_id IN %s",
                [tuple(student_ids)],
            )
            for tbl in ["clinic_sessionparticipant", "clinic_submission"]:
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                    ["public", tbl],
                )
                if cursor.fetchone():
                    print("DELETE", tbl)
                    cursor.execute(f"DELETE FROM {tbl} WHERE student_id IN %s", [tuple(student_ids)])
            print("DELETE students_student")
            cursor.execute("DELETE FROM students_student WHERE id IN %s", [tuple(student_ids)])
            print("All steps OK (no commit)")
    except Exception as e:
        import traceback
        print("FAILED:", type(e).__name__, str(e))
        traceback.print_exc()
        raise

if __name__ == "__main__":
    run()
