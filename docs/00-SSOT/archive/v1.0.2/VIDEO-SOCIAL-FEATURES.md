# Video Social Features Documentation (V1.0.2)

## Models

### VideoLike
- `video` FK → Video (CASCADE)
- `student` FK → Student (CASCADE)
- `tenant_id` PositiveIntegerField (db_index)
- `created_at` DateTimeField (auto_now_add)
- UniqueConstraint: (video, student) — prevents double-likes

### VideoComment
- `video` FK → Video (CASCADE)
- `tenant_id` PositiveIntegerField (db_index)
- `author_student` FK → Student (nullable)
- `author_staff` FK → Staff (nullable)
- `parent` FK → self (nullable) — 1-level threading
- `content` TextField (max_length=2000)
- `is_edited` BooleanField (default=False)
- `is_deleted` BooleanField (default=False)
- Properties: `author_type`, `author_name`, `author_photo_url`

### Video Model Additions
- `view_count` PositiveIntegerField (default=0)
- `like_count` PositiveIntegerField (default=0)
- `comment_count` PositiveIntegerField (default=0)

## API Endpoints

### Like Toggle
```
POST /api/v1/student/video/videos/{video_id}/like/
Response: { "liked": true/false, "like_count": N }
```

### Comment List + Create
```
GET /api/v1/student/video/videos/{video_id}/comments/
Response: { "comments": [...], "total": N }

POST /api/v1/student/video/videos/{video_id}/comments/
Body: { "content": "...", "parent_id": null }
Response: { "id": N, "content": "...", ... }
```

### Comment Edit + Delete
```
PATCH /api/v1/student/video/comments/{comment_id}/
Body: { "content": "updated text" }

DELETE /api/v1/student/video/comments/{comment_id}/
Response: { "deleted": true }
```

## Tenant Isolation

All endpoints enforce:
1. Video belongs to tenant (via session → lecture → tenant chain)
2. Student belongs to same tenant
3. Comments/likes filtered by tenant_id

## Frontend Components

### VideoCommentSection
- Location: `src/student/domains/video/components/VideoCommentSection.tsx`
- Features: CRUD, threading, teacher badge, relative time, avatars

### LikeButton
- Location: Inline in `VideoPlayerPage.tsx`
- Optimistic toggle with count display

### Info Section
- View count: Korean locale ("조회수 1,234회" / "1.2만회")
- Upload date: Relative time ("방금", "3분 전", "2시간 전", "3일 전")
- Duration display
