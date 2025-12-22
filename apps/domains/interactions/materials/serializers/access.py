from rest_framework import serializers
from ..models import MaterialAccess


class MaterialAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialAccess
        fields = "__all__"
