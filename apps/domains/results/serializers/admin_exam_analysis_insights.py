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


class ExamScoreSummarySerializer(serializers.Serializer):
    average = serializers.FloatField(allow_null=True)
    median = serializers.FloatField(allow_null=True)
    top_ten_average = serializers.FloatField(allow_null=True)
    highest = serializers.FloatField(allow_null=True)
    std_dev = serializers.FloatField(allow_null=True)
    std_rate = serializers.FloatField(allow_null=True)
    p25 = serializers.FloatField(allow_null=True)
    p75 = serializers.FloatField(allow_null=True)
    unscored_count = serializers.IntegerField()
    pass_criterion_count = serializers.IntegerField()
    pass_count = serializers.IntegerField()
    remediated_count = serializers.IntegerField()
    distribution_counts = serializers.ListField(
        child=serializers.IntegerField(min_value=0), min_length=5, max_length=5,
    )


class AdminExamAnalysisInsightsSerializer(serializers.Serializer):
    recommended_cutoff = serializers.IntegerField(allow_null=True)
    below_recommended_count = serializers.IntegerField(allow_null=True)
    scored_count = serializers.IntegerField()
    score_summary = ExamScoreSummarySerializer()
    trend_threshold_pp = serializers.IntegerField()
    trends = ExamScoreTrendSerializer(many=True)
