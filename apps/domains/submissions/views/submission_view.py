from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission


class SubmissionViewSet(ModelViewSet):
    queryset = Submission.objects.all().order_by("-id")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return SubmissionCreateSerializer
        return SubmissionSerializer

    def perform_create(self, serializer):
        submission = serializer.save(user=self.request.user)
        dispatch_submission(submission)
