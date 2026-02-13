# Community 도메인

**경로:** `apps/domains/community/`

## 구조

- **모델**: `Post`, `PostMapping`, `ScopeNode`, `BlockType`, `Reply`, `PostTemplate`
- **레이어**: `selectors/` (조회), `services/` (쓰기), `api/` (views/serializers/urls)
- **규칙**: interactions 부활 금지. models/ 하위 분리, api/ 하위 뷰/시리얼라이저, 서비스 경유 쓰기.

## 폴더 트리

```
community/
├── models/       block_type, scope_node, post, post_mapping, reply, post_template
├── selectors/    post_selector, scope_node_selector, block_type_selector
├── services/     community_service, scope_node_service
├── api/          views, serializers, urls
└── migrations/
```
