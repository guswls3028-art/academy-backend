# PATH: apps/support/video/views/admin_social_views.py
"""
Admin-side video social API (comments, engagement stats).
Teachers can view/reply to student comments on videos.
"""

from django.db.models import F
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.video.models import Video, VideoComment, VideoLike


def _get_video_with_tenant_check(video_id, request):
    """Get video and verify tenant isolation."""
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return None, None, Response(
            {"detail": "tenant required"}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        video = Video.objects.select_related("session__lecture").get(id=video_id)
    except Video.DoesNotExist:
        raise Http404

    video_tenant_id = (
        getattr(video.session.lecture, "tenant_id", None)
        if video.session and video.session.lecture
        else None
    )
    if video_tenant_id != tenant.id:
        raise PermissionDenied("접근 권한이 없습니다.")

    return video, tenant, None


def _get_staff(request):
    """Get the Staff record for the current user."""
    from apps.domains.staffs.models import Staff

    user = request.user
    tenant = getattr(request, "tenant", None)
    if not user or not tenant:
        return None
    return Staff.objects.filter(tenant=tenant, user=user).first()


def _serialize_comment(c, request, staff=None):
    """Serialize a comment for admin response."""
    photo_url = None
    if c.author_student and c.author_student.profile_photo:
        try:
            photo_url = request.build_absolute_uri(c.author_student.profile_photo.url)
        except Exception:
            pass
    elif c.author_staff and hasattr(c.author_staff, "profile_photo") and c.author_staff.profile_photo:
        try:
            photo_url = request.build_absolute_uri(c.author_staff.profile_photo.url)
        except Exception:
            pass

    is_mine = False
    if staff and c.author_staff_id == staff.id:
        is_mine = True

    return {
        "id": c.id,
        "content": c.content if not c.is_deleted else "",
        "author_type": c.author_type,
        "author_name": c.author_name,
        "author_photo_url": photo_url,
        "is_edited": c.is_edited,
        "is_deleted": c.is_deleted,
        "is_mine": is_mine,
        "created_at": c.created_at.isoformat(),
        "reply_count": (
            len([r for r in c.replies.all() if not r.is_deleted])
            if not c.is_deleted
            else 0
        ),
        "replies": [
            _serialize_comment(r, request, staff)
            for r in sorted(
                [r for r in c.replies.all() if not r.is_deleted],
                key=lambda r: r.created_at,
            )[:20]
        ],
    }


class AdminVideoCommentListView(APIView):
    """
    GET  /media/videos/{video_id}/comments/  — 댓글 목록 (관리자)
    POST /media/videos/{video_id}/comments/  — 댓글 작성 (선생님)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, video_id: int):
        video, tenant, err = _get_video_with_tenant_check(video_id, request)
        if err:
            return err

        staff = _get_staff(request)

        comments = (
            VideoComment.objects.filter(
                video=video, tenant_id=tenant.id, parent__isnull=True
            )
            .select_related("author_student", "author_staff")
            .prefetch_related("replies__author_student", "replies__author_staff")
            .order_by("-created_at")[:100]
        )

        data = [_serialize_comment(c, request, staff) for c in comments]
        return Response({"comments": data, "total": len(data)})

    def post(self, request, video_id: int):
        video, tenant, err = _get_video_with_tenant_check(video_id, request)
        if err:
            return err

        staff = _get_staff(request)
        if not staff:
            return Response(
                {"detail": "직원 정보가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content = str(request.data.get("content", "")).strip()
        if not content:
            return Response(
                {"detail": "댓글 내용을 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(content) > 2000:
            return Response(
                {"detail": "댓글은 2000자까지 입력할 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parent_id = request.data.get("parent_id")
        parent = None
        if parent_id:
            parent = VideoComment.objects.filter(
                id=parent_id, video=video, tenant_id=tenant.id, parent__isnull=True
            ).first()
            if not parent:
                return Response(
                    {"detail": "대댓글 대상을 찾을 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        comment = VideoComment.objects.create(
            video=video,
            tenant_id=tenant.id,
            author_staff=staff,
            parent=parent,
            content=content,
        )

        Video.objects.filter(id=video_id).update(
            comment_count=F("comment_count") + 1
        )

        photo_url = None
        if hasattr(staff, "profile_photo") and staff.profile_photo:
            try:
                photo_url = request.build_absolute_uri(staff.profile_photo.url)
            except Exception:
                pass

        return Response(
            {
                "id": comment.id,
                "content": comment.content,
                "author_type": "teacher",
                "author_name": staff.name,
                "author_photo_url": photo_url,
                "is_edited": False,
                "is_deleted": False,
                "is_mine": True,
                "created_at": comment.created_at.isoformat(),
                "reply_count": 0,
                "replies": [],
            },
            status=status.HTTP_201_CREATED,
        )


class AdminVideoCommentDetailView(APIView):
    """
    PATCH  /media/videos/comments/{comment_id}/  — 수정 (본인만)
    DELETE /media/videos/comments/{comment_id}/  — 삭제 (본인만)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def patch(self, request, comment_id: int):
        tenant = getattr(request, "tenant", None)
        staff = _get_staff(request)
        if not tenant or not staff:
            return Response(
                {"detail": "접근 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            comment = VideoComment.objects.get(id=comment_id, tenant_id=tenant.id)
        except VideoComment.DoesNotExist:
            raise Http404

        if comment.author_staff_id != staff.id:
            raise PermissionDenied("본인 댓글만 수정할 수 있습니다.")

        content = str(request.data.get("content", "")).strip()
        if not content:
            return Response(
                {"detail": "댓글 내용을 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(content) > 2000:
            return Response(
                {"detail": "댓글은 2000자까지 입력할 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        comment.content = content
        comment.is_edited = True
        comment.save(update_fields=["content", "is_edited", "updated_at"])

        return Response(
            {"id": comment.id, "content": comment.content, "is_edited": True}
        )

    def delete(self, request, comment_id: int):
        tenant = getattr(request, "tenant", None)
        staff = _get_staff(request)
        if not tenant or not staff:
            return Response(
                {"detail": "접근 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            comment = VideoComment.objects.get(id=comment_id, tenant_id=tenant.id)
        except VideoComment.DoesNotExist:
            raise Http404

        if comment.author_staff_id != staff.id:
            raise PermissionDenied("본인 댓글만 삭제할 수 있습니다.")

        comment.is_deleted = True
        comment.save(update_fields=["is_deleted", "updated_at"])

        Video.objects.filter(id=comment.video_id).update(
            comment_count=F("comment_count") - 1
        )
        Video.objects.filter(id=comment.video_id, comment_count__lt=0).update(
            comment_count=0
        )

        return Response({"deleted": True})


class AdminVideoEngagementView(APIView):
    """
    GET /media/videos/{video_id}/engagement/
    Returns view_count, like_count, comment_count for the video.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, video_id: int):
        video, tenant, err = _get_video_with_tenant_check(video_id, request)
        if err:
            return err

        return Response(
            {
                "view_count": video.view_count or 0,
                "like_count": video.like_count or 0,
                "comment_count": video.comment_count or 0,
            }
        )
