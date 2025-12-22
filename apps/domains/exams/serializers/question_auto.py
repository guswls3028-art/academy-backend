# apps/domains/exams/serializers/question_auto.py
from __future__ import annotations

from rest_framework import serializers


class QuestionAutoCreateSerializer(serializers.Serializer):
    """
    worker segmentation 결과 boxes를 그대로 받는다.
    boxes: [[x,y,w,h], ...]
    """
    boxes = serializers.ListField(
        child=serializers.ListField(
            child=serializers.IntegerField(),
            min_length=4,
            max_length=4,
        ),
        allow_empty=False,
    )

    def validate_boxes(self, v):
        # x,y,w,h 모두 0 이상, w/h는 1 이상
        out = []
        for row in v:
            x, y, w, h = row
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise serializers.ValidationError("Each box must be [x>=0, y>=0, w>0, h>0].")
            out.append([int(x), int(y), int(w), int(h)])
        return out
