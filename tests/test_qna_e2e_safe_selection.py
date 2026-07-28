from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.domains.students.models import Student
from apps.support.community.post_dependencies import student_user_for_qna_e2e


def _student(user_id: int):
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


def _query_chain():
    manager = MagicMock()
    queryset = MagicMock()
    manager.filter.return_value = queryset
    queryset.select_related.return_value = queryset
    queryset.order_by.return_value = queryset
    return manager, queryset


def test_auto_selection_is_tenant_scoped_and_requires_exactly_one():
    manager, queryset = _query_chain()
    expected = _student(101)
    queryset.__getitem__.return_value = [expected]

    with patch.object(Student, "objects", manager):
        selected = student_user_for_qna_e2e(tenant_id=7)

    assert selected is expected.user
    manager.filter.assert_called_once_with(
        tenant_id=7,
        deleted_at__isnull=True,
        user__isnull=False,
        user__is_active=True,
    )
    queryset.select_related.assert_called_once_with("user")
    queryset.order_by.assert_called_once_with("id")
    queryset.__getitem__.assert_called_once_with(slice(None, 2, None))

    queryset.__getitem__.return_value = [_student(102), _student(103)]
    with patch.object(Student, "objects", manager):
        assert student_user_for_qna_e2e(tenant_id=7) is None


def test_explicit_selection_stays_inside_the_tenant_scoped_active_queryset():
    manager, queryset = _query_chain()
    explicit = MagicMock()
    expected = _student(201)
    explicit.first.return_value = expected
    queryset.filter.return_value = explicit

    with patch.object(Student, "objects", manager):
        selected = student_user_for_qna_e2e(
            tenant_id=8,
            student_id=55,
        )

    assert selected is expected.user
    manager.filter.assert_called_once_with(
        tenant_id=8,
        deleted_at__isnull=True,
        user__isnull=False,
        user__is_active=True,
    )
    queryset.filter.assert_called_once_with(id=55)
