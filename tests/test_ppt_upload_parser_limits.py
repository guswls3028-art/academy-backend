from django.conf import settings

from apps.domains.tools.ppt.views import MAX_IMAGES


def test_django_multipart_file_limit_allows_ppt_max_images():
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES >= MAX_IMAGES


def test_large_ppt_batches_spool_files_before_default_memory_threshold():
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE <= 1024 * 1024
