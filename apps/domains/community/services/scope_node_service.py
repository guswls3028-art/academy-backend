"""ScopeNode 자동 생성: Lecture/Session 생성 시 대응 노드 1:1 생성."""
from apps.domains.community.models import ScopeNode


def ensure_scope_node_for_lecture(lecture) -> ScopeNode:
    """강의 1개당 COURSE 노드 1개. 있으면 반환, 없으면 생성."""
    node, _ = ScopeNode.objects.get_or_create(
        tenant_id=lecture.tenant_id,
        lecture_id=lecture.id,
        session_id=None,
        defaults={
            "level": ScopeNode.Level.COURSE,
            "parent_id": None,
        },
    )
    return node


def ensure_scope_node_for_session(session) -> ScopeNode:
    """차시 1개당 SESSION 노드 1개. 상위 COURSE 노드 확보 후 생성."""
    lecture = session.lecture
    parent = ensure_scope_node_for_lecture(lecture)
    node, _ = ScopeNode.objects.get_or_create(
        tenant_id=lecture.tenant_id,
        lecture_id=lecture.id,
        session_id=session.id,
        defaults={
            "level": ScopeNode.Level.SESSION,
            "parent_id": parent.id,
        },
    )
    return node
