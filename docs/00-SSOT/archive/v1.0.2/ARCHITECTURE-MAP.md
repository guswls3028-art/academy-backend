# V1.0.2 Architecture Map

## Backend Change Map

```
apps/
├── core/
│   ├── middleware/tenant.py      # +subscription check, +exempt paths
│   ├── models/program.py         # +subscription fields, +is_subscription_active
│   ├── serializers.py            # +subscription fields in ProgramPublicSerializer
│   ├── views.py                  # +SubscriptionView
│   ├── urls.py                   # +subscription/ endpoint
│   └── migrations/
│       ├── 0014_program_billing_email_and_more.py  # Schema
│       └── 0015_set_subscription_for_all_tenants.py # Data
├── domains/
│   ├── staffs/
│   │   ├── models.py             # +profile_photo ImageField
│   │   ├── serializers.py        # +profile_photo_url method
│   │   └── migrations/0005_staff_profile_photo.py
│   └── student_app/
│       ├── media/views.py        # +like, +comments, +view_count
│       ├── media/serializers.py  # +social fields
│       └── urls.py               # +social endpoints
└── support/
    └── video/
        ├── models.py             # +VideoLike, +VideoComment, +counters
        └── migrations/0008_videocomment_videolike_and_more.py
```

## Frontend Change Map

```
src/
├── app/
│   ├── App.tsx                   # +SubscriptionExpiredOverlay
│   └── router/AdminRouter.tsx    # +BillingSettingsPage route
├── features/
│   ├── settings/
│   │   ├── SettingsLayout.tsx    # +billing nav tab
│   │   └── pages/
│   │       ├── BillingSettingsPage.tsx      # NEW
│   │       └── BillingSettingsPage.module.css # NEW
│   ├── students/
│   │   ├── api/students.ts       # +applyDisplayNames, +displayName
│   │   ├── components/StudentsTable.tsx # +displayName rendering
│   │   └── pages/StudentsHomePage.tsx   # +displayName in confirm
│   └── lectures/
│       ├── components/EnrollStudentModal.tsx  # +displayName
│       ├── components/SessionEnrollModal.tsx  # +displayName
│       └── pages/lectures/LectureStudentsPage.tsx # +local displayName
├── shared/
│   ├── api/axios.ts              # +402 handler
│   └── ui/SubscriptionExpiredOverlay.tsx # NEW
└── student/
    ├── app/StudentApp.tsx        # +SubscriptionExpiredOverlay
    └── domains/
        ├── video/
        │   ├── api/video.ts      # +like, +comments API
        │   ├── components/VideoCommentSection.tsx # NEW
        │   ├── pages/VideoPlayerPage.tsx # +like, +info, +comments
        │   └── utils/timeAgo.ts  # NEW
        └── profile/
            └── pages/ProfilePage.tsx # +photo upload
```

## Data Flow

```
Subscription Check:
  Request → TenantMiddleware → resolve_tenant → _check_subscription
    → Program.is_subscription_active → 402 or continue

Video Like:
  POST /student/video/videos/{id}/like/
    → StudentVideoLikeView → toggle VideoLike → F() update like_count

Video Comment:
  POST /student/video/videos/{id}/comments/
    → StudentVideoCommentListView.post → create VideoComment
    → F() update comment_count

View Count:
  GET /student/video/videos/{id}/playback/
    → StudentVideoPlaybackView → F() increment view_count
```
