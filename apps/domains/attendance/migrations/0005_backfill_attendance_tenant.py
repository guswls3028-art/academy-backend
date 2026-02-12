# Generated migration: Backfill tenant for Attendance

from django.db import migrations


def backfill_attendance_tenant(apps, schema_editor):
    """
    Attendance의 tenant_id를 enrollment를 통해 채움
    
    Attendance -> Enrollment -> Student.tenant 또는 Lecture.tenant
    """
    Attendance = apps.get_model("attendance", "Attendance")
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Student = apps.get_model("students", "Student")
    Lecture = apps.get_model("lectures", "Lecture")
    
    # tenant가 null인 Attendance 레코드 처리
    attendances_without_tenant = Attendance.objects.filter(tenant__isnull=True).select_related('enrollment')
    
    updated_count = 0
    skipped_count = 0
    
    for attendance in attendances_without_tenant:
        try:
            enrollment = Enrollment.objects.select_related('student', 'lecture').get(id=attendance.enrollment_id)
            
            # 1순위: Enrollment의 tenant 사용
            if enrollment.tenant_id:
                attendance.tenant_id = enrollment.tenant_id
                attendance.save(update_fields=["tenant_id"])
                updated_count += 1
                continue
            
            # 2순위: Student를 통해 tenant 가져오기
            if enrollment.student_id:
                try:
                    student = Student.objects.get(id=enrollment.student_id)
                    if student.tenant_id:
                        attendance.tenant_id = student.tenant_id
                        attendance.save(update_fields=["tenant_id"])
                        updated_count += 1
                        continue
                except Student.DoesNotExist:
                    pass
            
            # 3순위: Lecture를 통해 tenant 가져오기
            if enrollment.lecture_id:
                try:
                    lecture = Lecture.objects.get(id=enrollment.lecture_id)
                    if lecture.tenant_id:
                        attendance.tenant_id = lecture.tenant_id
                        attendance.save(update_fields=["tenant_id"])
                        updated_count += 1
                        continue
                except Lecture.DoesNotExist:
                    pass
            
            # 모든 방법이 실패하면 건너뛰기
            skipped_count += 1
            print(f"Warning: Could not determine tenant for Attendance {attendance.id} (enrollment_id={attendance.enrollment_id})")
            
        except Enrollment.DoesNotExist:
            skipped_count += 1
            print(f"Warning: Enrollment {attendance.enrollment_id} not found for Attendance {attendance.id}")
        except Exception as e:
            skipped_count += 1
            print(f"Error processing Attendance {attendance.id}: {e}")
    
    print(f"Updated {updated_count} Attendance records with tenant_id")
    if skipped_count > 0:
        print(f"Skipped {skipped_count} Attendance records (could not determine tenant)")
    
    # 여전히 tenant가 null인 레코드가 있으면 경고
    remaining_null = Attendance.objects.filter(tenant__isnull=True).count()
    if remaining_null > 0:
        print(f"Warning: {remaining_null} Attendance records still have null tenant_id")
        print("These records need manual intervention before applying NOT NULL constraint")


def reverse_backfill(apps, schema_editor):
    """역방향 마이그레이션: tenant를 null로 설정 (필요시)"""
    # 일반적으로 역방향 마이그레이션은 필요 없음
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_remove_attendance_unique_attendance_per_session_and_more"),
        ("enrollment", "0002_add_tenant"),  # Enrollment 모델 필요
    ]

    operations = [
        migrations.RunPython(backfill_attendance_tenant, reverse_backfill),
    ]
