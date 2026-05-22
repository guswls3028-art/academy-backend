# 학생 생명주기 SSOT

**상태:** Active
**최종 점검:** 2026-05-23
**코드 기준:** `apps/domains/students/services/lifecycle.py`, `apps/domains/students/views/student_views.py`

## 1. 상태

| 상태 | 판정 | 진입 |
|------|------|------|
| Active | `Student.deleted_at IS NULL` | 학생 생성 또는 복원 |
| Soft-deleted | `Student.deleted_at IS NOT NULL` | `soft_delete_student()` |
| Restored | 다시 Active | `restore_student()` |
| Permanently deleted | DB row 제거 | `permanently_delete_students()` |

학생 삭제/복원/영구삭제 신규 코드는 view나 management command에 직접 구현하지 않는다.
HTTP와 운영 명령은 생명주기 서비스를 호출하는 compatibility facade다.

## 2. Soft Delete

SSOT: `soft_delete_student(student, tenant=...)`

- `deleted_at`을 기록하고 `ps_number`를 `_del_{student.id}_{old}`로 보존한다.
- `Parent` 직접 연결을 끊는다.
- 순수 학생 계정이면 해당 테넌트의 `student` 멤버십을 비활성화하고, 남은 활성 멤버십이 없을 때만 `User.is_active=False`로 둔다.
- 같은 사용자에게 다른 테넌트 멤버십이나 같은 테넌트의 staff/teacher/admin/owner/parent 역할이 남아 있으면 전역 계정을 잠그지 않는다.
- enrollment 비활성화와 clinic 예약 취소는 각 도메인 lifecycle hook을 통해 수행한다.

## 3. Restore

SSOT: `restore_student(student, tenant=..., profile_data=None)`

- `_del_` 접두사에서 원래 `ps_number`를 복원한다.
- 같은 테넌트 활성 학생과 아이디 충돌이 있으면 실패한다.
- `User.is_active`, 학생 전화번호, 테넌트 멤버십, Parent 연결을 복원한다.
- 복원은 비밀번호를 재발급하지 않는다. 가입 안내 알림톡도 새 비밀번호처럼 보내지 않는다.

## 4. Permanent Delete

SSOT: `permanently_delete_students(tenant=..., student_ids=[...])`

현재 facade:

- `StudentViewSet.bulk_permanent_delete`
- `StudentViewSet.bulk_resolve_conflicts`의 delete 후 재등록 경로
- `StudentViewSet.deleted_duplicates_fix`
- `check_deleted_student_duplicates --fix`
- `purge_deleted_students`

정리 범위:

- enrollment 및 enrollment child
- lecture section assignment
- fees: `StudentFee`, `StudentInvoice`, `InvoiceItem`, `FeePayment`
- submissions/results/homework/progress/video/clinic/community의 학생 참조
- 삭제 대상 테넌트의 student 멤버십과 pending password reset
- 다른 활성 멤버십·Parent·Staff·staff-role 멤버십이 없는 orphan `User`

안전 규칙:

- 삭제 대상 학생은 반드시 같은 tenant의 soft-deleted 학생이어야 한다.
- 같은 사용자가 다른 테넌트나 같은 테넌트의 비학생 역할로 남아 있으면 User와 해당 멤버십을 보존한다.
- 보존되는 사용자가 과거 soft delete 때문에 비활성화되어 있고 활성 멤버십이 남아 있으면 재활성화한다.
- tenant-owned child row가 다른 tenant로 깨져 있으면 조용히 삭제하지 않고 `cross_tenant_reference`로 중단한다.
- 현재 cross-domain 정리는 guarded raw SQL graph다. 장기 목표는 각 도메인 cleanup hook/event로 분해하는 것이다.

## 5. Retention 운영

- soft-deleted 학생은 30일 보관 후 purge 대상이다.
- 운영 스케줄은 EventBridge `academy-v1-purge-soft-deleted`: 매일 03:15 KST.
- 실행 명령은 API 컨테이너에서 `python manage.py purge_deleted_students`.
- 수동 점검:

```powershell
python manage.py check_deleted_student_duplicates --dry-run
python manage.py check_deleted_student_duplicates --fix
python manage.py purge_deleted_students --dry-run
python manage.py purge_deleted_students
```

## 6. 검증 기준

- soft delete, restore, permanent delete는 학생 생명주기 테스트에 포함되어야 한다.
- permanent delete 변경 시 최소 검증:
  - tenant isolation
  - cross-tenant User 보존 및 재활성화
  - same-tenant parent/staff/teacher 계정 보존
  - fee/section/video-comment dependency cleanup
  - corrupt cross-tenant child reference 차단
  - purge/duplicate cleanup command routing
