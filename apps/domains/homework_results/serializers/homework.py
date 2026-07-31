# PATH: apps/domains/homework_results/serializers/homework.py

from math import isfinite

from rest_framework import serializers

from apps.domains.homework_results.models import Homework


class HomeworkSerializer(serializers.ModelSerializer):
    max_score = serializers.FloatField(
        source="default_max_score",
        min_value=1,
        required=False,
    )

    class Meta:
        model = Homework
        fields = [
            "id",
            "homework_type",
            "template_homework",
            "session",
            "title",
            "max_score",
            "meta",
            "display_order",
            "updated_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "updated_at",
            "created_at",
        ]

    def validate(self, attrs):
        explicit_max_score = attrs.pop("default_max_score", None)
        if explicit_max_score is not None:
            meta = dict(attrs.get("meta", getattr(self.instance, "meta", None)) or {})
            meta["default_max_score"] = float(explicit_max_score)
            attrs["meta"] = meta

        if "meta" in attrs and isinstance(attrs["meta"], dict) and self.instance is not None:
            existing_meta = dict(getattr(self.instance, "meta", None) or {})
            if (
                "default_max_score" not in attrs["meta"]
                and "default_max_score" in existing_meta
            ):
                attrs["meta"] = {
                    **attrs["meta"],
                    "default_max_score": existing_meta["default_max_score"],
                }

        if "meta" in attrs and isinstance(attrs["meta"], dict):
            raw_max_score = attrs["meta"].get("default_max_score")
            if raw_max_score is not None:
                try:
                    parsed = float(raw_max_score)
                except (TypeError, ValueError, OverflowError):
                    raise serializers.ValidationError(
                        {"max_score": "만점은 1 이상의 숫자여야 합니다."}
                    )
                if not isfinite(parsed) or parsed < 1:
                    raise serializers.ValidationError(
                        {"max_score": "만점은 1 이상이어야 합니다."}
                    )
                attrs["meta"] = {
                    **attrs["meta"],
                    "default_max_score": parsed,
                }

        return attrs
