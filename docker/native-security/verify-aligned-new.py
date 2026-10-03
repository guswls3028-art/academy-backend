"""Exercise the system libstdc++ allocator for CVE-2026-95619, without writes.

The overflow inputs follow GCC's new_aligned_wrap.cc regression. This checks
the loaded system library, not statically linked or wheel-vendored C++ code.
"""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sysconfig


def verify_allocations(allocate, release, size_max):
    for size, alignment in ((64, 32), (128, 64), (0, 32)):
        pointer = allocate(size, alignment)
        if not pointer:
            raise RuntimeError("Normal aligned allocation failed")
        try:
            if pointer % alignment:
                raise RuntimeError("Normal allocation is misaligned")
        finally:
            release(pointer, alignment)
    cases = (
        (size_max - 8, 32), (size_max, 8), (size_max, 32),
        (size_max, 1024), (size_max, 65536), (size_max - 1, 16),
        (size_max - 1025, 1024), (size_max - 1024, 1024),
        (size_max - 65536, 65536),
    )
    for size, alignment in cases:
        pointer = allocate(size, alignment)
        if pointer:
            release(pointer, alignment)
            raise RuntimeError("Overflow allocation unexpectedly succeeded")
    return len(cases)


def main():
    import resource

    # A separate build-time process: never reserve a large address space, even
    # if the allocator regresses. No returned allocation is dereferenced.
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    if ctypes.sizeof(ctypes.c_size_t) != 8:
        raise RuntimeError("The release allocator contract requires a 64-bit image")
    library = Path("/usr/lib") / sysconfig.get_config_var("MULTIARCH") / "libstdc++.so.6"
    package = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}|${Version}", "libstdc++6"],
        capture_output=True, text=True, check=False,
    )
    if package.returncode == 1 and not library.exists():
        print("GCC_ALIGNED_NEW_SYSTEM_PACKAGE_ABSENT")
        return
    if package.returncode != 0 or not package.stdout.startswith("installed|"):
        raise RuntimeError("Unverified libstdc++ package state")
    library = library.resolve(strict=True)
    runtime = ctypes.CDLL(str(library))
    allocate = getattr(runtime, "_ZnwmSt11align_val_tRKSt9nothrow_t")
    allocate.argtypes = (ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p)
    allocate.restype = ctypes.c_void_p
    release = getattr(runtime, "_ZdlPvSt11align_val_t")
    release.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    release.restype = None
    nothrow = ctypes.c_char.in_dll(runtime, "_ZSt7nothrow")
    rejected = verify_allocations(
        lambda size, alignment: allocate(size, alignment, ctypes.addressof(nothrow)),
        release, 2**64 - 1,
    )
    print("GCC_ALIGNED_NEW_OVERFLOW_REJECTED=" + json.dumps({
        "packageVersion": package.stdout.strip().split("|", 1)[1],
        "librarySha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "normalAllocations": 3, "overflowRejected": rejected, "systemLibraryOnly": True,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
