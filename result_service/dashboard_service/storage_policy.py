"""Optional, per-store lossless filesystem compression for sealed replay data."""
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess


POLICY_FILE = ".uav-storage-policy.json"
LZX_POLICY = {"version": 1, "mode": "windows-lzx"}


def compression_enabled(output):
    marker = Path(output) / POLICY_FILE
    if marker.is_symlink():
        raise ValueError("The replay storage policy must be a regular file in the result store")
    try:
        with marker.open("rb") as stream:
            content = stream.read(2049)
    except FileNotFoundError:
        return False
    if len(content) > 2048 or json.loads(content) != LZX_POLICY:
        raise ValueError("Unknown replay storage policy")
    return True


@lru_cache(maxsize=1)
def _compressed_file_size_api():
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True).GetCompressedFileSizeW
    api.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.restype = wintypes.DWORD
    return api


def allocated_bytes(path):
    """Read Windows' compressed size, falling back to the full file length on error."""
    path = Path(path)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = _compressed_file_size_api()
        high = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        low = api(str(path.resolve()), ctypes.byref(high))
        # 0xffffffff is also a valid low DWORD when the call left last-error clear.
        if low != 0xffffffff or ctypes.get_last_error() == 0:
            return (high.value << 32) | low
    return path.stat().st_size


def compress_staged_sqlite(path):
    """Use Windows transparent LZX on a private copy; the caller validates its bytes."""
    if os.name != "nt":
        raise OSError("This store requests Windows LZX compression, unavailable on this operating system")
    executable = Path(os.environ["SystemRoot"]) / "System32" / "compact.exe"
    result = subprocess.run(
        [str(executable), "/C", "/F", "/EXE:LZX", str(Path(path).resolve())],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        message = result.stderr.decode("mbcs", errors="replace").strip()
        raise OSError(f"Windows could not compress the staged replay (code {result.returncode}): {message}")
