from unittest.mock import Mock

import pytest

from academy.application.use_cases.ai.pipelines import ppt_handler
from apps.shared.contracts.ai_job import AIJob


@pytest.mark.parametrize("payload", [
    {"mode": "pdf", "r2_key": "tenants/2/tools/ppt/tmp/job/source.pdf"},
    {"mode": "images", "r2_archive_key": "tenants/2/tools/ppt/tmp/job/images.zip"},
    {"mode": "images", "r2_keys": ["tenants/1/tools/ppt/tmp/job/1.png", "tenants/2/tools/ppt/tmp/job/2.png"]},
    {"mode": "pdf", "r2_key": "tenants/1/inventory/private.pdf"},
    {"mode": "pdf", "r2_key": "tenants/1/tools/ppt/tmp/job/source.pdf", "tenant_id": "2"},
    {"mode": "unknown", "r2_keys": ["tenants/1/tools/ppt/tmp/job/1.png"]},
    {"mode": "images", "r2_keys": "tenants/1/tools/ppt/tmp/job/1.png"},
])
def test_invalid_source_rejected_before_storage_or_rendering(monkeypatch, payload):
    download = Mock(side_effect=AssertionError("must validate before storage access"))
    monkeypatch.setattr("academy.adapters.ai.storage.downloader.download_r2_key_to_tmp", download)
    monkeypatch.setattr(ppt_handler, "_record_progress", lambda *a, **k: None)
    result = ppt_handler.handle_ppt_generation_job(AIJob.new(type="ppt_generation", tenant_id="1", payload=payload))
    assert result.status == "FAILED"
    download.assert_not_called()


def test_archive_member_limit_is_checked_before_decompression(tmp_path, monkeypatch):
    import zipfile

    archive = tmp_path / "images.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("0001.png", b"x" * 64)
    monkeypatch.setattr(ppt_handler, "MAX_ARCHIVED_IMAGE_BYTES", 32, raising=False)
    monkeypatch.setattr(ppt_handler, "_record_progress", lambda *a, **k: None)
    monkeypatch.setattr("academy.adapters.ai.storage.downloader.download_r2_key_to_tmp", lambda **k: str(archive))
    # Do not let the handler's owned temporary-directory cleanup remove pytest's fixture.
    monkeypatch.setattr(ppt_handler.shutil, "rmtree", lambda *a, **k: None)
    native_read = zipfile.ZipFile.read
    member_reads = []

    def checked_read(zf, member, *args, **kwargs):
        if str(zf.filename) == str(archive):
            member_reads.append(member)
        return native_read(zf, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", checked_read)
    job = AIJob.new(type="ppt_generation", tenant_id="1", payload={
        "mode": "images", "r2_archive_key": "tenants/1/tools/ppt/tmp/job/images.zip",
    })
    assert ppt_handler.handle_ppt_generation_job(job).status == "FAILED"
    assert member_reads == []


def test_legacy_images_stream_in_order_and_produce_reopenable_pptx(tmp_path, monkeypatch):
    import io

    from PIL import Image
    from pptx import Presentation
    from academy.application.use_cases.tools.generate_ppt import GeneratePptUseCase

    events = []
    payloads = []
    paths = []
    for index, color in enumerate(("red", "blue")):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = directory / "image.png"
        Image.new("RGB", (120, 80), color).save(path)
        paths.append(path)
    keys = [f"tenants/1/tools/ppt/tmp/job/{index}.png" for index in range(2)]

    def download(*, r2_key, job_id):
        index = keys.index(r2_key)
        events.append(f"download:{index}")
        return str(paths[index])

    native_execute = GeneratePptUseCase.execute

    def execute(self, images, **kwargs):
        def observed_images():
            for index, image in enumerate(images):
                events.append(f"consume:{index}")
                yield image
        return native_execute(self, observed_images(), **kwargs)

    monkeypatch.setattr(GeneratePptUseCase, "execute", execute)
    monkeypatch.setattr("academy.adapters.ai.storage.downloader.download_r2_key_to_tmp", download)
    monkeypatch.setattr(ppt_handler, "_record_progress", lambda *a, **k: None)
    monkeypatch.setattr("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage", lambda **k: payloads.append(k["fileobj"].read()))
    monkeypatch.setattr("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", lambda **k: "https://example.test/result.pptx")
    job = AIJob.new(type="ppt_generation", tenant_id="1", payload={"mode": "images", "r2_keys": keys})
    result = ppt_handler.handle_ppt_generation_job(job)
    assert result.status == "DONE"
    assert result.result["slide_count"] == 2
    assert events == ["download:0", "consume:0", "download:1", "consume:1"]
    assert len(Presentation(io.BytesIO(payloads[0])).slides) == 2
    assert all(not path.parent.exists() for path in paths)
