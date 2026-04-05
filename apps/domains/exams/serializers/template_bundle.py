# apps/domains/exams/serializers/template_bundle.py
from rest_framework import serializers
from apps.domains.exams.models.template_bundle import TemplateBundle, TemplateBundleItem


class TemplateBundleItemSerializer(serializers.ModelSerializer):
    template_title = serializers.SerializerMethodField()

    class Meta:
        model = TemplateBundleItem
        fields = [
            "id",
            "item_type",
            "exam_template",
            "homework_template",
            "title_override",
            "display_order",
            "config",
            "template_title",
        ]

    def get_template_title(self, obj: TemplateBundleItem) -> str:
        if obj.item_type == TemplateBundleItem.ItemType.EXAM and obj.exam_template:
            return obj.exam_template.title
        if obj.item_type == TemplateBundleItem.ItemType.HOMEWORK and obj.homework_template:
            return obj.homework_template.title
        return ""


class TemplateBundleSerializer(serializers.ModelSerializer):
    items = TemplateBundleItemSerializer(many=True, read_only=True)
    exam_count = serializers.SerializerMethodField()
    homework_count = serializers.SerializerMethodField()

    class Meta:
        model = TemplateBundle
        fields = [
            "id",
            "name",
            "description",
            "items",
            "exam_count",
            "homework_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_exam_count(self, obj: TemplateBundle) -> int:
        return sum(
            1 for i in obj.items.all()
            if i.item_type == TemplateBundleItem.ItemType.EXAM and i.exam_template_id is not None
        )

    def get_homework_count(self, obj: TemplateBundle) -> int:
        return sum(
            1 for i in obj.items.all()
            if i.item_type == TemplateBundleItem.ItemType.HOMEWORK and i.homework_template_id is not None
        )


class TemplateBundleCreateSerializer(serializers.Serializer):
    """묶음 생성/수정 — items 포함"""

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="", allow_blank=True)
    items = serializers.ListField(child=serializers.DictField(), required=False, default=list)

    def validate_items(self, value):
        for item in value:
            item_type = item.get("item_type")
            if item_type not in ("exam", "homework"):
                raise serializers.ValidationError(f"item_type은 exam 또는 homework여야 합니다: {item_type}")
            if item_type == "exam" and not item.get("exam_template_id"):
                raise serializers.ValidationError("exam 항목에는 exam_template_id가 필요합니다.")
            if item_type == "homework" and not item.get("homework_template_id"):
                raise serializers.ValidationError("homework 항목에는 homework_template_id가 필요합니다.")
        return value


class ApplyBundleSerializer(serializers.Serializer):
    """묶음 적용 요청"""

    session_id = serializers.IntegerField()
