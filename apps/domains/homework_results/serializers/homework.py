# PATH: apps/domains/homework_results/serializers/homework.py

from math import isfinite

from rest_framework import serializers

from apps.domains.homework_results.models import Homework
from apps.support.homework_results.homework_view_dependencies import (
    resolve_homework_cutline_settings,
)


class HomeworkSerializer(serializers.ModelSerializer):
    max_score = serializers.FloatField(
        source="default_max_score",
        min_value=1,
        required=False,
    )
    effective_cutline_mode = serializers.SerializerMethodField()
    effective_cutline_value = serializers.SerializerMethodField()
    effective_round_unit_percent = serializers.SerializerMethodField()
    uses_session_cutline_default = serializers.SerializerMethodField()
    source_exam_id = serializers.IntegerField(read_only=True)
    source_status = serializers.SerializerMethodField()
    source_filename = serializers.SerializerMethodField()
    source_question_count = serializers.SerializerMethodField()

    class Meta:
        model = Homework
        fields = [
            "id",
            "homework_type",
            "template_homework",
            "source_exam_id",
            "source_status",
            "source_filename",
            "source_question_count",
            "session",
            "title",
            "max_score",
            "cutline_mode",
            "cutline_value",
            "round_unit_percent",
            "effective_cutline_mode",
            "effective_cutline_value",
            "effective_round_unit_percent",
            "uses_session_cutline_default",
            "meta",
            "display_order",
            "updated_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "effective_cutline_mode",
            "effective_cutline_value",
            "effective_round_unit_percent",
            "uses_session_cutline_default",
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

        mode = attrs.get("cutline_mode", getattr(self.instance, "cutline_mode", None))
        value = attrs.get("cutline_value", getattr(self.instance, "cutline_value", None))
        round_unit = attrs.get(
            "round_unit_percent",
            getattr(self.instance, "round_unit_percent", None),
        )
        if (mode is None) != (value is None):
            raise serializers.ValidationError(
                {"cutline_value": "커트라인 기준과 값을 함께 설정해 주세요."}
            )
        if mode == Homework.CutlineMode.PERCENT and value is not None and value > 100:
            raise serializers.ValidationError(
                {"cutline_value": "퍼센트 커트라인은 100 이하이어야 합니다."}
            )
        if round_unit is not None and not 1 <= round_unit <= 50:
            raise serializers.ValidationError(
                {"round_unit_percent": "반올림 단위는 1부터 50까지 설정할 수 있습니다."}
            )
        candidate_max_score = Homework.max_score_from_meta(
            attrs.get("meta", getattr(self.instance, "meta", None))
        )
        if (
            mode == Homework.CutlineMode.COUNT
            and value is not None
            and float(value) > candidate_max_score
        ):
            raise serializers.ValidationError(
                {
                    "cutline_value": (
                        f"점수 커트라인({float(value):g}점)은 이 과제의 "
                        f"만점({candidate_max_score:g}점)을 넘을 수 없습니다."
                    )
                }
            )

        return attrs

    @staticmethod
    def _settings(obj: Homework):
        cached = getattr(obj, "_serialized_cutline_settings", None)
        if cached is None:
            cached = resolve_homework_cutline_settings(homework=obj)
            obj._serialized_cutline_settings = cached
        return cached

    def get_effective_cutline_mode(self, obj: Homework) -> str:
        return self._settings(obj).mode

    def get_effective_cutline_value(self, obj: Homework) -> int:
        return self._settings(obj).value

    def get_effective_round_unit_percent(self, obj: Homework) -> int:
        return self._settings(obj).round_unit_percent

    def get_uses_session_cutline_default(self, obj: Homework) -> bool:
        return self._settings(obj).uses_session_default

    @staticmethod
    def get_source_status(obj: Homework) -> str:
        return str(getattr(getattr(obj, "source_exam", None), "segmentation_status", "none") or "none")

    @staticmethod
    def get_source_filename(obj: Homework) -> str:
        return str(getattr(getattr(obj, "source_exam", None), "source_filename", "") or "")

    @staticmethod
    def get_source_question_count(obj: Homework) -> int:
        source_exam = getattr(obj, "source_exam", None)
        if not source_exam:
            return 0
        try:
            return int(source_exam.sheet.total_questions or 0)
        except Exception:
            return 0
