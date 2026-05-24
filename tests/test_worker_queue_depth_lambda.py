from infra.worker_asg.queue_depth_lambda import lambda_function as queue_depth_lambda


class FakeCloudWatch:
    def __init__(self):
        self.metric_batches = []

    def put_metric_data(self, Namespace, MetricData):
        self.metric_batches.append({"Namespace": Namespace, "MetricData": MetricData})


def _install_fake_clients(monkeypatch):
    sqs_client = object()
    cloudwatch_client = FakeCloudWatch()

    def fake_client(service_name, **_kwargs):
        if service_name == "sqs":
            return sqs_client
        if service_name == "cloudwatch":
            return cloudwatch_client
        raise AssertionError(f"unexpected boto3 client: {service_name}")

    monkeypatch.setattr(queue_depth_lambda.boto3, "client", fake_client)
    return cloudwatch_client


def _set_queue_names(monkeypatch):
    monkeypatch.setattr(queue_depth_lambda, "AI_QUEUE_LITE", "ai-lite")
    monkeypatch.setattr(queue_depth_lambda, "AI_QUEUE_BASIC", "ai-basic")
    monkeypatch.setattr(queue_depth_lambda, "VIDEO_QUEUE", "video-deprecated")
    monkeypatch.setattr(queue_depth_lambda, "MESSAGING_QUEUE", "messaging")


def test_queue_depth_lambda_skips_video_queue_lookup_when_video_metrics_disabled(monkeypatch):
    cloudwatch = _install_fake_clients(monkeypatch)
    _set_queue_names(monkeypatch)
    monkeypatch.setattr(queue_depth_lambda, "ENABLE_VIDEO_METRICS", False)
    calls = []

    def fake_get_queue_counts(_sqs_client, queue_name):
        calls.append(queue_name)
        return 1, 2

    monkeypatch.setattr(queue_depth_lambda, "get_queue_counts", fake_get_queue_counts)

    result = queue_depth_lambda.lambda_handler({}, None)

    assert calls == ["ai-lite", "ai-basic", "messaging"]
    assert "video_queue_depth" not in result
    assert [batch["Namespace"] for batch in cloudwatch.metric_batches] == ["Academy/Workers"]
    worker_types = [
        metric["Dimensions"][0]["Value"]
        for metric in cloudwatch.metric_batches[0]["MetricData"]
    ]
    assert worker_types == ["AI", "Messaging"]


def test_queue_depth_lambda_publishes_video_metrics_only_when_enabled(monkeypatch):
    cloudwatch = _install_fake_clients(monkeypatch)
    _set_queue_names(monkeypatch)
    monkeypatch.setattr(queue_depth_lambda, "ENABLE_VIDEO_METRICS", True)
    calls = []

    def fake_get_queue_counts(_sqs_client, queue_name):
        calls.append(queue_name)
        return 1, 2

    monkeypatch.setattr(queue_depth_lambda, "get_queue_counts", fake_get_queue_counts)

    result = queue_depth_lambda.lambda_handler({}, None)

    assert calls == ["ai-lite", "ai-basic", "video-deprecated", "messaging"]
    assert result["video_queue_depth"] == 1
    assert result["video_queue_depth_total"] == 3
    assert [batch["Namespace"] for batch in cloudwatch.metric_batches] == [
        "Academy/Workers",
        "Academy/VideoProcessing",
    ]
