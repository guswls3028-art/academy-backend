from django.db import transaction

from apps.domains.community.models import PostEntity, PostMapping, ScopeNode


class CommunityService:
    """PostEntity + PostMapping 생성/수정. 트랜잭션·중복 제거."""

    def __init__(self, tenant):
        self.tenant = tenant

    def create_post(self, data: dict, node_ids: list[int], *, include_children: bool = False):
        with transaction.atomic():
            post = PostEntity.objects.create(tenant=self.tenant, **{k: v for k, v in data.items() if v is not None})
            resolved_ids = self._resolve_node_ids_for_mapping(node_ids, include_children=include_children)
            self._replace_mappings_for_post(post.id, resolved_ids)
        return post

    def _resolve_node_ids_for_mapping(self, node_ids: list[int], *, include_children: bool = False) -> list[int]:
        if not node_ids:
            return []
        unique_ids = list(dict.fromkeys(int(node_id) for node_id in node_ids))
        nodes = ScopeNode.objects.filter(id__in=unique_ids, tenant=self.tenant).select_related("lecture")
        nodes_by_id = {int(node.id): node for node in nodes}
        if len(nodes_by_id) != len(unique_ids):
            raise ValueError("현재 학원에 속하지 않는 노드가 포함되어 있습니다.")
        seen = set()
        result = []
        for node_id in unique_ids:
            node = nodes_by_id[node_id]
            if node.id in seen:
                continue
            seen.add(node.id)
            result.append(node.id)
            if include_children and node.level == ScopeNode.Level.COURSE:
                for cid in ScopeNode.objects.filter(tenant=self.tenant, parent_id=node.id).values_list("id", flat=True):
                    if cid not in seen:
                        seen.add(cid)
                        result.append(cid)
        return result

    def update_post_nodes(self, post_id: int, node_ids: list[int]) -> None:
        with transaction.atomic():
            self._replace_mappings_for_post(post_id, node_ids)

    def _replace_mappings_for_post(self, post_id: int, node_ids: list[int]) -> None:
        post = PostEntity.objects.filter(id=post_id, tenant=self.tenant).first()
        if not post:
            return
        unique_ids = list(dict.fromkeys(int(node_id) for node_id in node_ids))
        valid_ids = set(ScopeNode.objects.filter(id__in=unique_ids, tenant=self.tenant).values_list("id", flat=True))
        if len(valid_ids) != len(unique_ids):
            raise ValueError("현재 학원에 속하지 않는 노드가 포함되어 있습니다.")
        existing = set(PostMapping.objects.filter(post_id=post_id).values_list("node_id", flat=True))
        to_remove = existing - set(unique_ids)
        to_add = [nid for nid in unique_ids if nid not in existing]
        if to_remove:
            PostMapping.objects.filter(post_id=post_id, node_id__in=to_remove).delete()
        if to_add:
            PostMapping.objects.bulk_create(
                [PostMapping(post_id=post_id, node_id=nid) for nid in to_add if nid in valid_ids]
            )
