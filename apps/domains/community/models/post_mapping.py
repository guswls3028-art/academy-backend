from django.db import models
from .post import PostEntity
from .scope_node import ScopeNode


class PostMapping(models.Model):
    """PostEntity ↔ ScopeNode M:N. 한 게시물을 여러 노드에 노출. UniqueConstraint(post, node)."""
    post = models.ForeignKey(
        PostEntity,
        on_delete=models.CASCADE,
        related_name="mappings",
    )
    node = models.ForeignKey(
        ScopeNode,
        on_delete=models.CASCADE,
        related_name="post_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["post", "node"],
                name="community_postmapping_post_node_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["node"]),
        ]

    def __str__(self):
        return f"Post#{self.post_id} → Node#{self.node_id}"
