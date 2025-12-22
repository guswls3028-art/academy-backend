from rest_framework import serializers
from ..models import Material


class MaterialSerializer(serializers.ModelSerializer):
    uploader_name = serializers.CharField(
        source="uploaded_by.name",
        read_only=True,
    )
    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
    )

    class Meta:
        model = Material
        fields = "__all__"
