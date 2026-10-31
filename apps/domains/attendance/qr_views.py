import uuid

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.attendance.qr_dependencies import IsStudent, Lecture, get_request_student
from .models import AttendanceQrPoint
from .services.qr import get_point, record, resolve


class QrPointSerializer(serializers.ModelSerializer):
    lecture_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    class Meta:
        model = AttendanceQrPoint
        fields = ["id", "label", "kind", "lecture_id", "location", "token", "active"]
        read_only_fields = ["id", "token"]

    def validate(self, attrs):
        if self.instance:
            for field in ("kind", "lecture_id", "location"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError("QR의 대상을 바꾸려면 새 QR을 만들어 주세요.")
            # An archived lecture must not prevent staff from revoking its printed sign.
            return attrs
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        lecture_id = attrs.get("lecture_id", getattr(self.instance, "lecture_id", None))
        location = attrs.get("location", getattr(self.instance, "location", ""))
        if kind == "class":
            if location or not Lecture.objects.filter(
                pk=lecture_id, tenant=self.context["request"].tenant, is_active=True, is_system=False,
            ).exists():
                raise serializers.ValidationError("현재 학원의 운영 중인 강의를 선택해 주세요.")
        elif lecture_id is not None:
            raise serializers.ValidationError("클리닉 QR에는 강의를 지정할 수 없습니다.")
        return attrs


class QrPointFilterSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["class", "clinic"], required=False)
    lecture_id = serializers.IntegerField(min_value=1, required=False)


class AttendanceQrPointViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = QrPointSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AttendanceQrPoint.objects.none()
        query = QrPointFilterSerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return AttendanceQrPoint.objects.filter(tenant=self.request.tenant, **query.validated_data)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        point = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
        serializer = self.get_serializer(point, data=request.data, partial=kwargs.pop("partial", False))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(request=None, responses=QrPointSerializer)
    @action(detail=True, methods=["post"])
    def rotate(self, request, pk=None):
        with transaction.atomic():
            point = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            point.token = uuid.uuid4()
            point.save(update_fields=["token"])
        return Response(self.get_serializer(point).data)


class QrPayload(serializers.Serializer):
    token = serializers.UUIDField()


class QrRecordPayload(QrPayload):
    target_id = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=["check_in", "check_out"])


class QrChoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField()
    date = serializers.DateField()
    start_time = serializers.TimeField(allow_null=True)
    status = serializers.CharField()
    checked_in_at = serializers.DateTimeField(allow_null=True)
    checked_out_at = serializers.DateTimeField(allow_null=True)
    can_check_in = serializers.BooleanField()
    can_check_out = serializers.BooleanField()


class QrPointInfoSerializer(serializers.Serializer):
    label = serializers.CharField()
    kind = serializers.CharField()


class QrResolutionSerializer(serializers.Serializer):
    point = QrPointInfoSerializer()
    choices = QrChoiceSerializer(many=True)


class QrReceiptSerializer(serializers.Serializer):
    detail = serializers.CharField()
    already_recorded = serializers.BooleanField()
    kind = serializers.CharField()
    target_id = serializers.IntegerField()
    status = serializers.CharField()
    recorded_at = serializers.DateTimeField(allow_null=True)
    checked_in_at = serializers.DateTimeField(allow_null=True)
    checked_out_at = serializers.DateTimeField(allow_null=True)


class QrStudentThrottle(UserRateThrottle):
    rate = "30/min"
    scope = "student_attendance_qr"


class StudentQrResolveView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]
    throttle_classes = [QrStudentThrottle]

    @extend_schema(request=QrPayload, responses=QrResolutionSerializer)
    def post(self, request):
        payload = QrPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        student = get_request_student(request)
        if student is None:
            raise PermissionDenied("학생 본인 계정으로 로그인해 주세요.")
        point = get_point(tenant=request.tenant, **payload.validated_data)
        return Response(resolve(tenant=request.tenant, student=student, point=point))


class StudentQrRecordView(StudentQrResolveView):
    @extend_schema(request=QrRecordPayload, responses=QrReceiptSerializer)
    def post(self, request):
        payload = QrRecordPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        student = get_request_student(request)
        if student is None:
            raise PermissionDenied("학생 본인 계정으로 로그인해 주세요.")
        return Response(record(
            tenant=request.tenant, student=student, actor=request.user, **payload.validated_data,
        ))
