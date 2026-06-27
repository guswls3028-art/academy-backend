import importlib

from PIL import Image


def test_clip_image_embedding_downscales_large_inputs(monkeypatch, tmp_path):
    svc = importlib.import_module("academy.adapters.ai.embedding.image_service")
    monkeypatch.setattr(svc, "_CLIP_MAX_IMAGE_SIDE", 512)
    monkeypatch.setattr(svc, "_CLIP_MAX_IMAGE_PIXELS", 512 * 512)
    monkeypatch.setattr(svc, "_CLIP_HARD_SKIP_IMAGE_PIXELS", 10_000_000)

    seen_sizes = []

    class FakeClipModel:
        def encode(self, images, **kwargs):
            seen_sizes.extend(img.size for img in images)
            return [[0.1] * 512 for _ in images]

    monkeypatch.setattr(svc, "_get_clip_model", lambda: FakeClipModel())

    path = tmp_path / "large.png"
    Image.new("RGB", (2400, 1200), "white").save(path)

    batch = svc.get_image_embeddings([str(path)])

    assert len(batch.vectors) == 1
    assert len(batch.vectors[0]) == 512
    assert seen_sizes
    width, height = seen_sizes[0]
    assert max(width, height) <= 512
    assert width * height <= 512 * 512


def test_clip_image_embedding_skips_hard_cap_inputs(monkeypatch, tmp_path):
    svc = importlib.import_module("academy.adapters.ai.embedding.image_service")
    monkeypatch.setattr(svc, "_CLIP_MAX_IMAGE_SIDE", 512)
    monkeypatch.setattr(svc, "_CLIP_MAX_IMAGE_PIXELS", 512 * 512)
    monkeypatch.setattr(svc, "_CLIP_HARD_SKIP_IMAGE_PIXELS", 100_000)

    class FakeClipModel:
        def encode(self, images, **kwargs):
            raise AssertionError("hard-skipped images should not reach CLIP")

    monkeypatch.setattr(svc, "_get_clip_model", lambda: FakeClipModel())

    path = tmp_path / "huge.png"
    Image.new("RGB", (400, 400), "white").save(path)

    batch = svc.get_image_embeddings([str(path)])

    assert batch.vectors == [[]]
