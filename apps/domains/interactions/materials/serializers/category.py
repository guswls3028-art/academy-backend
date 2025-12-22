from rest_framework import serializers
from ..models import MaterialCategory


class MaterialCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialCategory
        fields = "__all__"
