"""Public fixtures for cross-domain homework remediation integration tests."""

from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_remediation_service import ClinicRemediationService


def create_homework_clinic_link_fixture(**kwargs):
    """Create an unresolved homework link through the progress-owned fixture boundary."""
    return ClinicLink.objects.create(source_type="homework", reason="AUTO_FAILED", **kwargs)


def submit_homework_retake_fixture(**kwargs):
    """Exercise the real remediation transaction from cross-domain regression tests."""
    return ClinicRemediationService.submit_homework_retake(**kwargs)
