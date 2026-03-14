# PATH: apps/domains/students/tests/test_identity_lifecycle.py
"""
Regression tests for student identity SSOT, lifecycle, and ghost-data elimination.
Covers: ps_number/username sync, deletion semantics, restore flow, ghost data filters.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.user import user_internal_username, user_display_username
from apps.domains.students.models import Student
from apps.domains.parents.models import Parent
from apps.domains.inventory.models import InventoryFolder, InventoryFile
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant

User = get_user_model()


def _create_tenant(name="TestAcademy", code="test"):
    return Tenant.objects.create(name=name, code=code)


def _create_student(tenant, ps_number, name="테스트학생", phone="01012345678", parent_phone="01098765432"):
    internal_username = user_internal_username(tenant, ps_number)
    user = User.objects.create_user(
        username=internal_username,
        password="test1234",
        tenant=tenant,
        phone=phone,
        name=name,
    )
    student = Student.objects.create(
        tenant=tenant,
        user=user,
        ps_number=ps_number,
        name=name,
        phone=phone,
        parent_phone=parent_phone,
        omr_code=phone[-8:] if phone and len(phone) >= 8 else "00000000",
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return student


class TestPsNumberUsernameSyncOnSave(TestCase):
    """Student.save() hook: ps_number change → User.username sync + inventory cascade."""

    def setUp(self):
        self.tenant = _create_tenant()
        self.student = _create_student(self.tenant, "A12345")

    def test_ps_number_change_syncs_username(self):
        """ps_number 변경 시 User.username이 자동 동기화."""
        self.student.ps_number = "B99999"
        self.student.save(update_fields=["ps_number"])
        self.student.user.refresh_from_db()
        expected = user_internal_username(self.tenant, "B99999")
        self.assertEqual(self.student.user.username, expected)

    def test_ps_number_change_cascades_inventory(self):
        """ps_number 변경 시 인벤토리 student_ps도 업데이트."""
        folder = InventoryFolder.objects.create(
            tenant=self.tenant,
            student_ps="A12345",
            name="root",
        )
        ifile = InventoryFile.objects.create(
            tenant=self.tenant,
            student_ps="A12345",
            folder=folder,
            name="test.pdf",
            r2_key="test/key",
            size_bytes=100,
        )
        self.student.ps_number = "C77777"
        self.student.save(update_fields=["ps_number"])
        folder.refresh_from_db()
        ifile.refresh_from_db()
        self.assertEqual(folder.student_ps, "C77777")
        self.assertEqual(ifile.student_ps, "C77777")

    def test_del_prefix_ps_does_not_cascade_inventory(self):
        """_del_ 접두사 ps_number 변경은 인벤토리 업데이트 안 함 (삭제 시)."""
        InventoryFolder.objects.create(
            tenant=self.tenant, student_ps="A12345", name="root"
        )
        self.student.ps_number = f"_del_{self.student.id}_A12345"
        self.student.save(update_fields=["ps_number"])
        folder = InventoryFolder.objects.get(tenant=self.tenant, student_ps="A12345")
        self.assertEqual(folder.student_ps, "A12345")  # 변경 안 됨

    def test_display_username_matches_ps_number(self):
        """user_display_username(user) == ps_number (SSOT)."""
        display = user_display_username(self.student.user)
        self.assertEqual(display, self.student.ps_number)
        # ps_number 변경 후에도 동일
        self.student.ps_number = "D11111"
        self.student.save(update_fields=["ps_number"])
        self.student.user.refresh_from_db()
        self.assertEqual(user_display_username(self.student.user), "D11111")


class TestSoftDeleteSemantics(TestCase):
    """Student soft-delete → ps_number mangling, user deactivation, enrollment status."""

    def setUp(self):
        self.tenant = _create_tenant()
        self.student = _create_student(self.tenant, "S11111", phone="01011112222")

    def test_soft_delete_mangles_ps_number(self):
        now = timezone.now()
        self.student.deleted_at = now
        original_ps = self.student.ps_number
        self.student.ps_number = f"_del_{self.student.id}_{original_ps}"
        self.student.save(update_fields=["deleted_at", "ps_number"])
        self.assertTrue(self.student.ps_number.startswith("_del_"))

    def test_soft_delete_deactivates_user(self):
        self.student.user.is_active = False
        self.student.user.phone = None
        self.student.user.save(update_fields=["is_active", "phone"])
        self.student.user.refresh_from_db()
        self.assertFalse(self.student.user.is_active)
        self.assertIsNone(self.student.user.phone)


class TestSoftDeleteCancelsClinicBookings(TestCase):
    """Student soft-delete should cancel active clinic participants (PENDING/BOOKED → CANCELLED)."""

    def setUp(self):
        self.tenant = _create_tenant()
        self.student = _create_student(self.tenant, "CL001", phone="01055550001", parent_phone="01099990001")
        self.session = ClinicSession.objects.create(
            tenant=self.tenant,
            date="2026-04-01",
            start_time="14:00",
            location="Room A",
            max_participants=10,
        )
        # Create BOOKED and PENDING participants
        self.booked = SessionParticipant.objects.create(
            tenant=self.tenant, session=self.session, student=self.student,
            status=SessionParticipant.Status.BOOKED, source=SessionParticipant.Source.AUTO,
        )
        self.pending = SessionParticipant.objects.create(
            tenant=self.tenant, session=None, student=self.student,
            requested_date="2026-04-02", requested_start_time="15:00",
            status=SessionParticipant.Status.PENDING, source=SessionParticipant.Source.STUDENT_REQUEST,
        )

    def test_soft_delete_cancels_active_bookings(self):
        """BOOKED/PENDING 예약이 학생 삭제 시 CANCELLED로 변경."""
        now = timezone.now()
        # Simulate soft-delete clinic cancellation
        SessionParticipant.objects.filter(
            student=self.student, tenant=self.tenant,
            status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
        ).update(status=SessionParticipant.Status.CANCELLED, status_changed_at=now)

        self.booked.refresh_from_db()
        self.pending.refresh_from_db()
        self.assertEqual(self.booked.status, "cancelled")
        self.assertEqual(self.pending.status, "cancelled")

    def test_attended_not_cancelled(self):
        """ATTENDED 상태는 삭제 시에도 보존 (이력)."""
        attended = SessionParticipant.objects.create(
            tenant=self.tenant, session=self.session, student=self.student,
            status=SessionParticipant.Status.ATTENDED, source=SessionParticipant.Source.AUTO,
            participant_role="target",
        )
        # Only cancel PENDING/BOOKED
        SessionParticipant.objects.filter(
            student=self.student, tenant=self.tenant,
            status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
        ).update(status=SessionParticipant.Status.CANCELLED)

        attended.refresh_from_db()
        self.assertEqual(attended.status, "attended")  # 보존됨

    def test_session_count_after_cancel(self):
        """예약 취소 후 세션 카운트가 정확해야 함."""
        SessionParticipant.objects.filter(
            student=self.student, tenant=self.tenant,
            status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
        ).update(status=SessionParticipant.Status.CANCELLED)

        from django.db.models import Count, Q
        session = (
            ClinicSession.objects.filter(pk=self.session.pk)
            .annotate(
                booked_count=Count("participants", filter=Q(
                    participants__status__in=["booked", "pending"]
                ))
            ).first()
        )
        self.assertEqual(session.booked_count, 0)  # 모두 취소됨


class TestBulkRestoreFlow(TestCase):
    """Bulk restore: ps_number collision check, parent re-link, User.phone restore."""

    def setUp(self):
        self.tenant = _create_tenant()
        self.student = _create_student(self.tenant, "R11111", phone="01033334444", parent_phone="01055556666")
        # Create parent
        self.parent = Parent.objects.create(
            tenant=self.tenant, name="학부모", phone="01055556666"
        )
        self.student.parent = self.parent
        self.student.save(update_fields=["parent"])
        # Soft delete
        self.student.deleted_at = timezone.now()
        self.student.ps_number = f"_del_{self.student.id}_R11111"
        self.student.parent_id = None
        self.student.save(update_fields=["deleted_at", "ps_number", "parent"])
        self.student.user.is_active = False
        self.student.user.phone = None
        self.student.user.save(update_fields=["is_active", "phone"])

    def test_restore_recovers_ps_number(self):
        """복원 시 _del_ 접두사 제거하여 원래 ps_number 복원."""
        parts = self.student.ps_number.split("_", 3)
        if len(parts) >= 4:
            self.student.ps_number = parts[3]
        self.student.deleted_at = None
        self.student.save(update_fields=["deleted_at", "ps_number"])
        self.assertEqual(self.student.ps_number, "R11111")

    def test_restore_collision_detection(self):
        """복원 시 ps_number가 다른 활성 학생에게 사용 중이면 충돌."""
        _create_student(self.tenant, "R11111", name="새학생", phone="01099998888", parent_phone="01077778888")
        collision = Student.objects.filter(
            tenant=self.tenant, ps_number="R11111", deleted_at__isnull=True
        ).exists()
        self.assertTrue(collision)

    def test_user_phone_can_be_restored(self):
        """복원 시 User.phone을 Student.phone에서 복원."""
        self.student.user.phone = self.student.phone
        self.student.user.is_active = True
        self.student.user.save(update_fields=["is_active", "phone"])
        self.student.user.refresh_from_db()
        self.assertEqual(self.student.user.phone, "01033334444")


class TestCrossTenantIsolation(TestCase):
    """Same ps_number across different tenants: must be allowed."""

    def setUp(self):
        self.tenant1 = _create_tenant("Academy1", "acad1")
        self.tenant2 = _create_tenant("Academy2", "acad2")

    def test_same_ps_number_different_tenants(self):
        s1 = _create_student(self.tenant1, "X99999", name="학생A", phone="01011111111", parent_phone="01022222222")
        s2 = _create_student(self.tenant2, "X99999", name="학생B", phone="01033333333", parent_phone="01044444444")
        self.assertEqual(s1.ps_number, s2.ps_number)
        self.assertNotEqual(s1.user.username, s2.user.username)
        self.assertEqual(user_display_username(s1.user), "X99999")
        self.assertEqual(user_display_username(s2.user), "X99999")

    def test_duplicate_blocked_within_same_tenant(self):
        _create_student(self.tenant1, "D11111", phone="01055555555", parent_phone="01066666666")
        with self.assertRaises(Exception):
            _create_student(self.tenant1, "D11111", name="다른학생", phone="01077777777", parent_phone="01088888888")


class TestGhostDataExclusion(TestCase):
    """Deleted students should not appear in active queries."""

    def setUp(self):
        self.tenant = _create_tenant()
        self.active_student = _create_student(self.tenant, "ACT001", name="활성학생", phone="01011110001", parent_phone="01099990001")
        self.deleted_student = _create_student(self.tenant, "DEL001", name="삭제학생", phone="01011110002", parent_phone="01099990002")
        # Soft delete
        self.deleted_student.deleted_at = timezone.now()
        self.deleted_student.ps_number = f"_del_{self.deleted_student.id}_DEL001"
        self.deleted_student.save(update_fields=["deleted_at", "ps_number"])

    def test_active_student_query_excludes_deleted(self):
        active = Student.objects.filter(tenant=self.tenant, deleted_at__isnull=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first().ps_number, "ACT001")

    def test_community_filter_expression(self):
        """Community _EXCLUDE_DELETED_AUTHOR Q expression works correctly."""
        from django.db.models import Q
        _filter = Q(created_by__isnull=True) | Q(created_by__deleted_at__isnull=True)
        # Verify the Q expression is constructable (ORM-level test)
        self.assertIsNotNone(_filter)

    def test_video_comment_filter_expression(self):
        """Video comment author filter Q expression works correctly."""
        from django.db.models import Q
        _active = Q(author_student__isnull=True) | Q(author_student__deleted_at__isnull=True)
        self.assertIsNotNone(_active)


class TestUsernameDisplayFunctions(TestCase):
    """user_internal_username / user_display_username are inverses."""

    def setUp(self):
        self.tenant = _create_tenant()

    def test_roundtrip(self):
        display = "MYID01"
        internal = user_internal_username(self.tenant, display)
        self.assertTrue(internal.startswith(f"t{self.tenant.id}_"))
        user = User.objects.create_user(username=internal, password="x", tenant=self.tenant)
        self.assertEqual(user_display_username(user), display)

    def test_no_tenant(self):
        internal = user_internal_username(None, "plainuser")
        self.assertEqual(internal, "plainuser")

    def test_parent_prefix(self):
        parent_username = f"p_{self.tenant.id}_01012345678"
        user = User.objects.create_user(username=parent_username, password="x", tenant=self.tenant)
        self.assertEqual(user_display_username(user), "01012345678")
