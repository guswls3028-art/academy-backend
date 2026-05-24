import zipfile
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.domains.tools.ppt.views import _build_images_archive
from apps.shared.contracts.ai_job import AIJob
from academy.application.use_cases.ai.pipelines.ppt_handler import handle_ppt_generation_job


def test_build_images_archive_preserves_ordered_entries():
    files = [
        SimpleUploadedFile("문항 10.png", b"first", content_type="image/png"),
        SimpleUploadedFile("bad/name?.jpg", b"second", content_type="image/jpeg"),
    ]

    archive = _build_images_archive(files)
    try:
        with zipfile.ZipFile(archive) as zf:
            assert zf.namelist() == ["images/0000.png", "images/0001.jpg"]
            assert zf.read("images/0000.png") == b"first"
            assert zf.read("images/0001.jpg") == b"second"

        assert files[0].tell() == 0
        assert files[1].tell() == 0
    finally:
        archive.close()


def test_ppt_worker_accepts_single_image_archive(monkeypatch, tmp_path):
    download_dir = tmp_path / "download"
    download_dir.mkdir()
    archive_path = download_dir / "images.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("images/0000.png", b"first")
        zf.writestr("images/0001.png", b"second")

    captured = {}

    def fake_download_r2_key_to_tmp(*, r2_key: str, job_id: str) -> str:
        captured["download"] = (r2_key, job_id)
        return str(archive_path)

    class FakeGeneratePptUseCase:
        def execute(self, image_bytes_list, config=None, on_progress=None, total_count=None):
            captured["images"] = list(image_bytes_list)
            captured["config"] = config
            captured["total_count"] = total_count
            if on_progress:
                on_progress(100, "PPT 생성 중")
            return SimpleNamespace(pptx_bytes=b"PK fake pptx", slide_count=len(captured["images"]))

    def fake_upload_fileobj_to_r2_storage(*, fileobj, key: str, content_type: str):
        captured["upload"] = (key, content_type, fileobj.read())

    monkeypatch.setattr(
        "academy.adapters.ai.storage.downloader.download_r2_key_to_tmp",
        fake_download_r2_key_to_tmp,
    )
    monkeypatch.setattr(
        "academy.application.use_cases.tools.generate_ppt.GeneratePptUseCase",
        FakeGeneratePptUseCase,
    )
    monkeypatch.setattr(
        "apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage",
        fake_upload_fileobj_to_r2_storage,
    )
    monkeypatch.setattr(
        "apps.infrastructure.storage.r2.generate_presigned_get_url_storage",
        lambda **kwargs: "https://example.test/result.pptx",
    )
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.ppt_handler._record_progress",
        lambda *args, **kwargs: None,
    )

    job = AIJob(
        id="job-archive",
        type="ppt_generation",
        tenant_id="1",
        payload={
            "mode": "images",
            "r2_archive_key": "tenants/1/tools/ppt/tmp/job/images.zip",
            "config": {"aspect_ratio": "16:9", "background": "black", "fit_mode": "contain"},
            "settings": {},
            "tenant_id": "1",
        },
    )

    result = handle_ppt_generation_job(job)

    assert result.status == "DONE"
    assert captured["download"] == ("tenants/1/tools/ppt/tmp/job/images.zip", "job-archive-archive")
    assert captured["images"] == [b"first", b"second"]
    assert captured["total_count"] == 2
    assert captured["upload"][1] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert result.result["slide_count"] == 2
