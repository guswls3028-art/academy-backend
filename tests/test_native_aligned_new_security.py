import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_aligned_new", ROOT / "docker/native-security/verify-aligned-new.py"
)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_safe_allocator_accepts_normal_inputs_and_rejects_overflow():
    allocations = []
    freed = []

    def allocate(size, alignment):
        allocations.append((size, alignment))
        return 65536 if size <= 128 else None

    assert verifier.verify_allocations(allocate, lambda *args: freed.append(args), 2**64 - 1) == 9
    assert len(allocations) == 12
    assert len(freed) == 3


def test_wrapping_allocator_fails_instead_of_treating_small_pointer_as_success():
    freed = []
    with pytest.raises(RuntimeError, match="Overflow allocation unexpectedly succeeded"):
        verifier.verify_allocations(lambda *_: 65536, lambda *args: freed.append(args), 2**64 - 1)
    assert len(freed) == 4


def test_always_rejecting_allocator_cannot_pass_without_positive_control():
    with pytest.raises(RuntimeError, match="Normal aligned allocation failed"):
        verifier.verify_allocations(lambda *_: None, lambda *_: None, 2**64 - 1)


def test_misaligned_allocation_fails_and_is_released():
    freed = []
    with pytest.raises(RuntimeError, match="misaligned"):
        verifier.verify_allocations(lambda *_: 3, lambda *args: freed.append(args), 2**64 - 1)
    assert freed == [(3, 32)]


def test_runtime_images_recheck_allocator_after_package_installation():
    base = (ROOT / "docker/Dockerfile.base").read_text(encoding="utf-8")
    assert "docker/native-security/verify-aligned-new.py" in base
    assert "python /usr/local/bin/verify-aligned-new.py" in base
    for service in ("api", "video-worker", "ai-worker-cpu", "tools-worker"):
        dockerfile = (ROOT / "docker" / service / "Dockerfile").read_text(encoding="utf-8")
        assert dockerfile.rfind("apt-get install") < dockerfile.rfind("python /usr/local/bin/verify-aligned-new.py")
