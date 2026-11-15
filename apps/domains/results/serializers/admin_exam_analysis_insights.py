from rest_framework import serializers


class ExamScoreTrendSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    lecture_title = serializers.CharField()
    current_rate = serializers.FloatField()
    previous_session_rate = serializers.FloatField(allow_null=True)
    cumulative_rate = serializers.FloatField()
    prior_exam_count = serializers.IntegerField()
    previous_delta_pp = serializers.FloatField(allow_null=True)
    cumulative_delta_pp = serializers.FloatField()
    signal = serializers.ChoiceField(choices=["up", "down"], allow_null=True)


class AdminExamAnalysisInsightsSerializer(serializers.Serializer):
    recommended_cutoff = serializers.IntegerField(allow_null=True)
    below_recommended_count = serializers.IntegerField(allow_null=True)
    scored_count = serializers.IntegerField()
    trend_threshold_pp = serializers.IntegerField()
    trends = ExamScoreTrendSerializer(many=True)
