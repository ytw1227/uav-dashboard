"""Read the existing S/B offline HTML packages. No JavaScript or research code is run."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import mmap
import re
import runpy
from dataclasses import dataclass
from pathlib import Path


# Keep scans and hashes bounded even when a package contains hundreds of MiB.
_SCAN_BYTES = 4 * 1024 * 1024


def _discard_pages(file: mmap.mmap, start: int, end: int) -> None:
    """Release clean mapped pages when supported; the read-only mapping stays valid."""
    advise = getattr(file, "madvise", None)
    advice = getattr(mmap, "MADV_DONTNEED", None)
    if advise is None or advice is None or end <= start:
        return
    # Unix requires a page-aligned start. A partial final page is allowed.
    aligned = start - start % mmap.PAGESIZE
    try:
        advise(advice, aligned, end - aligned)
    except (OSError, ValueError):
        # Advice is optional on each platform and must not change file parsing.
        pass


def _find(file: mmap.mmap, needle: bytes, start: int = 0, end: int | None = None) -> int:
    """Find a marker without retaining every page crossed by a full-package scan."""
    end = len(file) if end is None else end
    while start < end:
        stop = min(start + _SCAN_BYTES, end)
        found = file.find(needle, start, stop)
        _discard_pages(file, start, stop)
        if found >= 0:
            return found
        if stop == end:
            break
        # Keep matches which straddle the next search window intact.
        start = stop - len(needle) + 1
    return -1


def _digest(file: mmap.mmap, start: int, end: int) -> str:
    """Hash this opened mapping, without reopening a possibly replaced path."""
    digest = hashlib.sha256()
    for offset in range(start, end, _SCAN_BYTES):
        stop = min(offset + _SCAN_BYTES, end)
        with memoryview(file)[offset:stop] as window:
            digest.update(window)
        _discard_pages(file, offset, stop)
    return digest.hexdigest()


@dataclass(frozen=True)
class _Block:
    attrs: dict[str, str]
    start: int
    body_start: int
    body_end: int
    end: int


def _blocks(file: mmap.mmap) -> dict[str, _Block]:
    """Index byte spans, without loading or decoding all flights in the 70-map package."""
    result = {}
    cursor = 0
    while (start := _find(file, b"<script", cursor)) >= 0:
        opening_end = _find(file, b">", start)
        end = _find(file, b"</script>", opening_end + 1)
        if opening_end == -1 or end == -1:
            raise ValueError("Incomplete script element in offline HTML")
        cursor = end + len(b"</script>")
        # The package generator emits quoted attributes and escapes closing script tags.
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', file[start:opening_end].decode("utf-8")))
        key = attrs.get("id")
        if key is not None:
            if key in result:
                raise ValueError(f"Duplicate offline block: {key}")
            result[key] = _Block(attrs, start, opening_end + 1, end, cursor)
    return result


class PortableDashboard:
    """One read-only snapshot of one trusted local package. Open once per source.

    codec_dir points to the project's existing codec_v2.py and metadata_pack.py.
    The app owner configures this Python source directory, never the HTML file.
    Catalogs and read results belong to this source; treat them as read-only.
    """

    def __init__(self, html_path: str | Path, *, codec_dir: str | Path):
        self.path = Path(html_path).resolve()
        codec_dir = Path(codec_dir).resolve()
        # These existing, standard-library-only codecs are also used by the S/B builder.
        # run_path avoids modifying import search paths or writing to the original project.
        self._decode_columns = runpy.run_path(str(codec_dir / "codec_v2.py"))["decode"]
        self._decode_metadata = runpy.run_path(str(codec_dir / "metadata_pack.py"))["unpack_metadata"]
        with self.path.open("rb") as file:
            self._file = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            self._blocks = _blocks(self._file)
            # Attribute reads may fault neighbouring pages back in after a scan.
            # Drop the index scan's pages before allocating the decoded catalog.
            _discard_pages(self._file, 0, len(self._file))
            self.catalog = self.read_chunk("portable-catalog")
            if self.catalog.get("format") != "uav-dashboard-offline-v1":
                raise ValueError("Unsupported offline catalog format")
            self._margins = None
        except BaseException:
            self.close()
            raise

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def entry(self, run_id: str, revision: str) -> dict:
        """Resolve a pinned run/revision in this package; never choose another revision."""
        entry = self.catalog["entries"][run_id]
        if entry["revision"] != revision:
            raise KeyError(f"Revision {revision!r} not present for {run_id!r}")
        manifest = entry["manifest"]
        if (manifest["run_id"], manifest["bundle_revision"]) != (run_id, revision):
            raise ValueError(f"Inconsistent manifest identity for {run_id!r}")
        return entry

    def block_digest(self, chunk_id: str) -> str:
        """Hash the complete original script element, matching S's build verification."""
        block = self._blocks[chunk_id]
        return _digest(self._file, block.start, block.end)

    def file_digest(self) -> str:
        """Identify the same opened snapshot used by this reader."""
        return _digest(self._file, 0, len(self._file))

    def has_chunk(self, chunk_id: str) -> bool:
        return chunk_id in self._blocks

    def block_bytes(self, chunk_id: str) -> bytes:
        block = self._blocks[chunk_id]
        body = self._file[block.body_start:block.body_end]
        _discard_pages(self._file, block.body_start, block.body_end)
        return body

    def packed_chunk(self, chunk_id: str) -> tuple[str, bytes]:
        """Original gzip stream and its payload codec, for browser-side decoding."""
        attrs = self._blocks[chunk_id].attrs
        if attrs.get("type") != "application/octet-stream":
            raise ValueError(f"{chunk_id!r} is not a packed data block")
        body = self.block_bytes(chunk_id)
        encoding = attrs.get("data-encoding", "base64")
        if encoding == "base85":
            packed = base64.b85decode(body.replace(b":", b"<"))
        elif encoding == "base64":
            packed = base64.b64decode(body, validate=True)
        else:
            raise ValueError(f"Unsupported encoding {encoding!r} for {chunk_id!r}")
        return attrs.get("data-codec", "json"), packed

    def read_chunk(self, chunk_id: str):
        """Decode one JSON data block, preserving original names, mask values and units."""
        block = self._blocks[chunk_id]
        mime = block.attrs.get("type")
        if mime not in {"application/json", "application/octet-stream"} or chunk_id == "plotly-runtime":
            raise ValueError(f"{chunk_id!r} is a display runtime, not a JSON data block")
        if mime == "application/json":
            return json.loads(self.block_bytes(chunk_id))
        _, packed = self.packed_chunk(chunk_id)
        raw = gzip.decompress(packed)
        codec = block.attrs.get("data-codec")
        if codec == "lossless-columns":
            return self._decode_columns(raw)
        if codec == "metadata-dag":
            return self._decode_metadata(json.loads(raw))
        if codec is not None:
            raise ValueError(f"Unsupported codec {codec!r} for {chunk_id!r}")
        return json.loads(raw)

    def margin(self, run_id: str, revision: str) -> dict | None:
        """Return the verified UI series for this run/revision, or None when unavailable."""
        self.entry(run_id, revision)
        if self._margins is None:
            block = self._blocks["portable-app"]
            module = b'modules["margin_data"]=(function(){'
            start = _find(self._file, module, block.body_start, block.body_end)
            if start < 0:
                self._margins = {}
            else:
                # The S builder wraps the exporter-generated JSON in a JS module.
                # Parse its literal only; don't evaluate the surrounding script.
                module_end = _find(self._file, b"\nreturn {", start, block.body_end)
                if module_end < 0:
                    raise ValueError("Malformed margin_data module in portable-app")
                declaration = b"\nconst marginRecords = "
                start = _find(self._file, declaration, start, module_end)
                if start < 0:
                    raise ValueError("Missing marginRecords declaration in margin_data")
                literal = self._file[start + len(declaration):module_end].strip()
                _discard_pages(self._file, start, module_end)
                self._margins = json.loads(literal.removesuffix(b";"))
                if not isinstance(self._margins, dict):
                    raise ValueError("Invalid marginRecords collection")
        record = self._margins.get(run_id)
        if record is not None and record["revision"] != revision:
            raise ValueError(f"Margin data revision mismatch for {run_id!r}")
        return record
