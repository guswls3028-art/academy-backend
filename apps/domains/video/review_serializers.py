from rest_framework import serializers


class ReviewWatchSummarySerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    applicable = serializers.BooleanField()
    used_ms = serializers.IntegerField()
    limit_ms = serializers.IntegerField(allow_null=True)
    remaining_ms = serializers.IntegerField(allow_null=True)
    exhausted = serializers.BooleanField()
    revision = serializers.CharField()
