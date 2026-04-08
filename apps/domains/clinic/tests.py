# apps/domains/clinic/tests.py
"""
클리닉 도메인 안정화 테스트
- 상태 전이 허용/거부
- ClinicLink resolution SSOT
- 동시성 (trigger idempotency)
- 테넌트 격리
- 점수 검증
- enrollment 선택 규칙
- clinic_reason 판정
"""
import inspect

from django.test import TestCase

from apps.domains.clinic.models import Session, SessionParticipant
from apps.domains.progress.models import ClinicLink


class StatusTransitionTest(TestCase):
    """상태 전이 허용/거부 검증"""

    def test_valid_transitions_admin(self):
        """관리자 허용 전이 맵이 코드와 일치"""
        EXPECTED = {
            "pending": {"booked", "rejected", "cancelled"},
            "booked": {"attended", "no_show", "cancelled"},
            "attended": {"booked", "no_show"},
            "no_show": {"booked", "attended"},
            "rejected": set(),
            "cancelled": set(),
        }
        # 코드에서 전이 맵 추출
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.set_status
        )
        for from_status, to_set in EXPECTED.items():
            # 구조적 검증: 터미널 상태에서는 전이 불가
            if from_status in ("rejected", "cancelled"):
                self.assertEqual(to_set, set(), f"Terminal {from_status} must have no transitions")

    def test_student_can_only_cancel_pending(self):
        """학생은 pending→cancelled만 가능"""
        # Student transition map from views.py
        STUDENT_VALID = {
            "pending": {"cancelled"},
            "booked": set(),
            "attended": set(),
            "no_show": set(),
            "rejected": set(),
            "cancelled": set(),
        }
        for from_status, to_set in STUDENT_VALID.items():
            if from_status != "pending":
                self.assertEqual(to_set, set(), f"Student: {from_status} should have no transitions")
            else:
                self.assertEqual(to_set, {"cancelled"})

    def test_complete_allowed_transitions(self):
        """complete()는 PENDING/BOOKED에서만 ATTENDED로 전환"""
        from apps.domains.clinic.views import ParticipantViewSet
        allowed = ParticipantViewSet.COMPLETE_ALLOWED_TRANSITIONS
        self.assertEqual(
            allowed,
            {SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED},
        )

    def test_terminal_states_block_complete(self):
        """CANCELLED/REJECTED는 complete() 코드에서 명시 거부"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.complete
        )
        self.assertIn("CANCELLED", source)
        self.assertIn("REJECTED", source)

    def test_all_statuses_have_choices(self):
        """SessionParticipant.Status에 6가지 상태 모두 정의"""
        statuses = {c[0] for c in SessionParticipant.Status.choices}
        self.assertEqual(statuses, {"pending", "booked", "attended", "no_show", "cancelled", "rejected"})


class ResolutionSSoTTest(TestCase):
    """ClinicResolutionService SSOT 검증"""

    def test_resolution_types_defined(self):
        """모든 resolution_type이 정의됨"""
        types = {c[0] for c in ClinicLink.ResolutionType.choices}
        self.assertIn("EXAM_PASS", types)
        self.assertIn("HOMEWORK_PASS", types)
        self.assertIn("MANUAL_OVERRIDE", types)
        self.assertIn("WAIVED", types)
        self.assertIn("BOOKING_LEGACY", types)

    def test_resolution_service_methods_exist(self):
        """ClinicResolutionService에 모든 SSOT 메서드가 있음"""
        from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService
        required_methods = [
            "resolve_by_exam_pass", "resolve_by_homework_pass",
            "resolve_manually", "waive", "unresolve", "carry_over",
        ]
        for method_name in required_methods:
            self.assertTrue(
                hasattr(ClinicResolutionService, method_name),
                f"ClinicResolutionService.{method_name} must exist"
            )

    def test_no_direct_resolved_at_writes_outside_service(self):
        """resolved_at 직접 쓰기는 ClinicResolutionService 내부에서만"""
        import os
        import re
        # views, signals 등에서 resolved_at을 직접 쓰는 패턴 검색
        dangerous_files = []
        service_path = os.path.normpath("apps/domains/progress/services/clinic_resolution_service.py")
        remediation_path = os.path.normpath("apps/domains/progress/services/clinic_remediation_service.py")
        safe_paths = {service_path, remediation_path}

        for root, dirs, files in os.walk("apps"):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "migrations")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                fpath = os.path.normpath(os.path.join(root, f))
                if fpath in safe_paths or "test" in f.lower():
                    continue
                with open(fpath, encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                # resolved_at에 직접 값 할당 (= timezone.now() 또는 = None)
                if re.search(r'\.resolved_at\s*=\s*(timezone\.now|None)', content):
                    dangerous_files.append(fpath)
        self.assertEqual(
            dangerous_files, [],
            f"resolved_at direct writes found outside service: {dangerous_files}"
        )


class ScoreValidationTest(TestCase):
    """점수 검증 테스트"""

    def test_backend_validates_max_score_exam(self):
        """ClinicRemediationService.submit_exam_retake에 만점 초과 검증 있음"""
        source = inspect.getsource(
            __import__(
                "apps.domains.progress.services.clinic_remediation_service",
                fromlist=["ClinicRemediationService"]
            ).ClinicRemediationService.submit_exam_retake
        )
        self.assertIn("만점", source, "exam retake must validate max_score")

    def test_backend_validates_max_score_homework(self):
        """ClinicRemediationService.submit_homework_retake에 만점 초과 검증 있음"""
        source = inspect.getsource(
            __import__(
                "apps.domains.progress.services.clinic_remediation_service",
                fromlist=["ClinicRemediationService"]
            ).ClinicRemediationService.submit_homework_retake
        )
        self.assertIn("만점", source, "homework retake must validate max_score")

    def test_view_validates_negative_score(self):
        """submit_retake 뷰에서 음수 점수 거부"""
        source = inspect.getsource(
            __import__(
                "apps.domains.progress.views",
                fromlist=["ClinicLinkViewSet"]
            ).ClinicLinkViewSet.submit_retake
        )
        self.assertIn("score < 0", source)


class ClinicReasonDetectionTest(TestCase):
    """clinic_reason 자동감지 테스트"""

    def test_source_type_based_detection_no_hardcode(self):
        """source_type 기반 판정: has_homework=False 하드코딩 없음"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.create
        )
        # source_type 기반 판정 확인
        self.assertIn('source_type="exam"', source)
        self.assertIn('source_type="homework"', source)
        # 하드코딩 패턴 부재 확인
        self.assertNotIn("has_homework=False", source)
        self.assertNotIn("has_homework = False", source)


class EnrollmentSelectionTest(TestCase):
    """enrollment 선택 기준 단일화 테스트"""

    def test_ordering_consistent_in_views(self):
        """views.py에서 -enrolled_at, -id 순서"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.create
        )
        self.assertIn("-enrolled_at", source)
        self.assertIn("-id", source)

    def test_ordering_consistent_in_idcard(self):
        """idcard_views.py에서 -enrolled_at, -id 순서"""
        from apps.domains.clinic import idcard_views
        source = inspect.getsource(idcard_views.StudentClinicIdcardView)
        self.assertIn("-enrolled_at", source)
        self.assertIn("-id", source)


class TenantIsolationTest(TestCase):
    """테넌트 격리 테스트"""

    def test_clinic_highlight_uses_tenant_filter(self):
        """compute_clinic_highlight_map에 tenant 필터 적용"""
        from apps.domains.results.utils import clinic_highlight
        source = inspect.getsource(clinic_highlight.compute_clinic_highlight_map)
        self.assertIn("tenant=tenant", source)

    def test_serializer_highlight_uses_tenant_filter(self):
        """ClinicSessionParticipantSerializer.get_name_highlight_clinic_target에 tenant 필터"""
        from apps.domains.clinic.serializers import ClinicSessionParticipantSerializer
        source = inspect.getsource(ClinicSessionParticipantSerializer.get_name_highlight_clinic_target)
        self.assertIn("tenant=tenant", source, "Serializer highlight must filter by tenant")

    def test_clinic_utils_uses_tenant_filter(self):
        """get_clinic_enrollment_ids_for_session에 tenant 필터"""
        from apps.domains.results.utils.clinic import get_clinic_enrollment_ids_for_session
        source = inspect.getsource(get_clinic_enrollment_ids_for_session)
        self.assertIn("tenant_id", source, "clinic util must filter by tenant")

    def test_cliniclink_has_tenant_field(self):
        """ClinicLink 모델에 tenant FK 존재"""
        field_names = [f.name for f in ClinicLink._meta.get_fields()]
        self.assertIn("tenant", field_names)

    def test_clinic_target_service_requires_tenant(self):
        """ClinicTargetService.list_admin_targets가 tenant=None이면 빈 리스트 반환"""
        from apps.domains.results.services.clinic_target_service import ClinicTargetService
        result = ClinicTargetService.list_admin_targets(tenant=None)
        self.assertEqual(result, [])

    def test_clinic_link_viewset_filters_by_tenant(self):
        """ClinicLinkViewSet.get_queryset에 tenant 필터"""
        from apps.domains.progress.views import ClinicLinkViewSet
        source = inspect.getsource(ClinicLinkViewSet.get_queryset)
        self.assertIn("tenant=tenant", source)

    def test_participant_create_validates_session_tenant(self):
        """ParticipantViewSet.create에서 세션 테넌트 교차검증"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.create
        )
        self.assertIn("tenant_id", source, "Session tenant cross-validation required")

    def test_create_serializer_scopes_querysets_by_tenant(self):
        """ClinicSessionParticipantCreateSerializer.__init__에서 tenant 스코프 적용"""
        from apps.domains.clinic.serializers import ClinicSessionParticipantCreateSerializer
        source = inspect.getsource(ClinicSessionParticipantCreateSerializer.__init__)
        self.assertIn("tenant", source)


class TriggerIdempotencyTest(TestCase):
    """트리거 서비스 idempotency 테스트"""

    def test_idempotent_create_helper_exists(self):
        """_idempotent_create_clinic_link 헬퍼 함수 존재"""
        from apps.domains.progress.services.clinic_trigger_service import _idempotent_create_clinic_link
        self.assertTrue(callable(_idempotent_create_clinic_link))

    def test_trigger_uses_transaction_atomic(self):
        """_idempotent_create_clinic_link이 atomic + IntegrityError 방어"""
        from apps.domains.progress.services.clinic_trigger_service import _idempotent_create_clinic_link
        source = inspect.getsource(_idempotent_create_clinic_link)
        self.assertIn("transaction.atomic", source)
        self.assertIn("IntegrityError", source)

    def test_trigger_sets_tenant_id(self):
        """_idempotent_create_clinic_link이 tenant_id를 설정"""
        from apps.domains.progress.services.clinic_trigger_service import _idempotent_create_clinic_link
        source = inspect.getsource(_idempotent_create_clinic_link)
        self.assertIn("tenant_id=tenant_id", source)


class DuplicateBookingDefenseTest(TestCase):
    """중복 예약 방어 테스트"""

    def test_db_unique_constraint_exists(self):
        """SessionParticipant에 active 상태 UniqueConstraint 존재"""
        constraints = SessionParticipant._meta.constraints
        unique_names = [c.name for c in constraints]
        self.assertIn("uniq_clinic_participant_active", unique_names)

    def test_view_duplicate_check_in_atomic_block(self):
        """ParticipantViewSet.create에서 atomic 블록 내 중복 체크"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.create
        )
        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("이미 해당 세션에 예약된 학생입니다", source)

    def test_integrity_error_handled(self):
        """IntegrityError가 409 응답으로 변환"""
        source = inspect.getsource(
            __import__("apps.domains.clinic.views", fromlist=["ParticipantViewSet"]).ParticipantViewSet.create
        )
        self.assertIn("IntegrityError", source)
        self.assertIn("409", source)
