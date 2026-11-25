"""Loopback-only media and pinned data for the NiceGUI playback components."""
from __future__ import annotations

import gzip
import hashlib
import json
import runpy
from functools import lru_cache
from pathlib import Path
from threading import RLock

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from .portable import PortableDashboard
from .region_selection import presentation_catalog


VENDOR = Path(__file__).parent / "vendor"


def compressed_json(data):
    return gzip.compress(json.dumps(data, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode(), compresslevel=2, mtime=0)


class PackageMedia:
    """Share immutable recordings only. Player and camera state live with each client."""

    def __init__(self, paths, codec_dir, digests):
        self.paths = paths
        self.codec_dir = Path(codec_dir)
        self.digests = digests
        self.opened = {}
        self.lock = RLock()
        self.sidecar_status = {}

    def package(self, source):
        with self.lock:
            if source not in self.paths:
                raise KeyError(source)
            if source not in self.opened:
                store = PortableDashboard(self.paths[source], codec_dir=self.codec_dir)
                if store.file_digest() != self.digests[source]:
                    store.close()
                    raise ValueError(f"{source} 공유 파일이 검증된 기준 버전과 다릅니다.")
                self.opened[source] = store
            return self.opened[source]

    def close(self):
        with self.lock:
            for store in self.opened.values():
                store.close()
            self.opened.clear()
            self.catalog_bytes.cache_clear()
            self.adapter.cache_clear()
            self.margin_module.cache_clear()
            self.packed.cache_clear()

    @lru_cache(maxsize=2)
    def catalog_bytes(self, source):
        catalog = self.catalog(source)
        # The adapter only needs fleet and duration to window the recordings.
        # Retrieve a complete manifest, including definitions and provenance, on selection.
        entries = {}
        for rid, entry in catalog["entries"].items():
            manifest = entry["manifest"]
            entries[rid] = {**entry, "manifest": {key: manifest[key] for key in ("fleet", "simulation_end_s")}}
        return compressed_json({**catalog, "entries": entries})

    def catalog(self, source):
        return presentation_catalog(self.package(source).catalog, source)

    @lru_cache(maxsize=12)
    def packed(self, source, chunk):
        return self.package(source).packed_chunk(chunk)

    @lru_cache(maxsize=2)
    def adapter(self, source):
        store = self.package(source)
        original = store.block_bytes("portable-adapter").decode()
        start = original.index("      const node = document.getElementById('portable-catalog');")
        end = original.index("      if (!Array.isArray(saved.runs)", start)
        code = original[:start] + "      saved = window.__NICEGUI_CATALOG__;\n" + original[end:]
        start = code.index("      if (typeof DecompressionStream")
        end = code.index("      cache.set(id, value);", start)
        fetch_data = """
      const response = await fetch(ROOT + '/packed/' + encodeURIComponent(id));
      if (!response.ok) fail('기록 조각을 읽을 수 없습니다: ' + id + ' · ' + response.status);
      const codec = response.headers.get('X-UAV-Codec');
      let value;
      if (codec === 'lossless-columns') {
        value = window.__LOSSLESS_DECODE__(new Uint8Array(await response.arrayBuffer()));
      } else if (codec === 'json') {
        value = await response.json();
      } else { fail('지원하지 않는 기록 형식: ' + codec); }
"""
        root = json.dumps("/part2/data/" + source)
        code = code[:start] + fetch_data.replace("ROOT", root) + code[end:]
        manifest = "if (match[2] === 'manifest') return item.manifest;"
        if code.count(manifest) != 1:
            raise ValueError("공유본의 manifest 읽기 계약이 바뀌었습니다.")
        code = code.replace(manifest, f"""if (match[2] === 'manifest') {{
          const response = await fetch({root} + '/manifest/' + encodeURIComponent(id) + '/' + encodeURIComponent(item.revision));
          if (!response.ok) fail('선택한 비행의 manifest를 읽지 못했습니다.');
          return response.json();
        }}""")
        # The NiceGUI shell owns the catalog, selection, notices and navigation.
        code = code.rstrip().removesuffix("window.__PORTABLE__.mount();")
        decoder = runpy.run_path(str(self.codec_dir / "codec_v2.py"))["JS_DECODER"]
        return decoder + "\n" + code

    @lru_cache(maxsize=2)
    def margin_module(self, source):
        target = self.package(source)
        records = {}

        def collect(artifact):
            for rid, item in artifact.catalog["entries"].items():
                ref = target.catalog["entries"].get(rid)
                if ref and ref["revision"] == item["revision"] and ref["manifest"]["map_hash"] == item["manifest"]["map_hash"]:
                    record = artifact.margin(rid, item["revision"])
                    if record is not None:
                        records[rid] = record

        collect(target)
        if source == "B":
            try:
                collect(self.package("S"))
            except (ValueError, OSError) as exc:
                self.sidecar_status["S"] = str(exc)
            try:
                raw = (VENDOR / "regional_margins.json.gz").read_bytes()
                pin = json.loads((VENDOR / "sangam/baseline.json").read_bytes())
                if hashlib.sha256(raw).hexdigest() != pin["files"]["../regional_margins.json.gz"]:
                    raise ValueError("지역별 여유 자료의 해시가 다릅니다.")
                for rid, info in json.loads(gzip.decompress(raw)).items():
                    item = target.catalog["entries"].get(rid)
                    if item and item["revision"] == info["record"]["revision"] and item["manifest"]["map_hash"] == info["map_hash"]:
                        records[rid] = info["record"]
            except (ValueError, OSError, KeyError, EOFError) as exc:
                self.sidecar_status["regional_margins"] = str(exc)
        # Same thresholds as the proven margin_data module, with source-specific records.
        visible = self.catalog(source)["entries"]
        records = {rid: value for rid, value in records.items() if rid in visible}
        return ("export const warningThresholds = {enter:5, exit:7};\nexport const marginRecords = "
                + json.dumps(records, ensure_ascii=False, separators=(",", ":")) + ";")


def register_package_routes(app, media):
    base = "/part2/data/{source}"
    frozen = {"Cache-Control": "private, max-age=3600"}

    def store(source):
        try:
            return media.package(source)
        except KeyError:
            raise HTTPException(404, "자료 출처를 찾을 수 없습니다.")
        except (OSError, ValueError) as exc:
            raise HTTPException(503, str(exc))

    def zipped(raw, mime, **extra):
        return Response(raw, media_type=mime, headers={**frozen, "Content-Encoding": "gzip", **extra})

    @app.get(base + "/catalog")
    def catalog(source: str):
        store(source)
        # B is a current presentation index, unlike the immutable flight chunks.
        headers = {"Cache-Control": "private, no-cache"} if source == "B" else {}
        return zipped(media.catalog_bytes(source), "application/json", **headers)

    @app.get(base + "/manifest/{rid}/{revision}")
    def manifest(source: str, rid: str, revision: str):
        package = store(source)
        try:
            return zipped(compressed_json(package.entry(rid, revision)["manifest"]), "application/json")
        except KeyError:
            raise HTTPException(404, "해당 실행·변환본이 없습니다.")

    @app.get(base + "/packed/{chunk_id}")
    def packed(source: str, chunk_id: str):
        store(source)
        if chunk_id in {"plotly-runtime", "portable-catalog", "portable-app", "portable-adapter"}:
            raise HTTPException(404, "기록 조각을 찾을 수 없습니다.")
        try:
            codec, data = media.packed(source, chunk_id)
            return zipped(data, "application/octet-stream", **{"X-UAV-Codec": codec})
        except (KeyError, ValueError):
            raise HTTPException(404, "압축된 기록 조각을 찾을 수 없습니다.")

    @app.get(base + "/chunk/{chunk_id}")
    def json_chunk(source: str, chunk_id: str):
        package = store(source)
        if chunk_id in {"portable-catalog", "plotly-runtime"}:
            raise HTTPException(404)
        try:
            return zipped(compressed_json(package.read_chunk(chunk_id)), "application/json")
        except (KeyError, ValueError):
            raise HTTPException(404, "자료 조각을 찾을 수 없습니다.")

    @app.get(base + "/plotly.js")
    def plotly(source: str):
        store(source)
        _, data = media.packed(source, "plotly-runtime")
        return zipped(data, "application/javascript")

    @app.get(base + "/adapter.js")
    def adapter(source: str):
        store(source)
        return Response(media.adapter(source), media_type="application/javascript", headers=frozen)

    @app.get(base + "/margins.js")
    def margins(source: str):
        store(source)
        return zipped(gzip.compress(media.margin_module(source).encode(), compresslevel=3),
                      "application/javascript")

    @app.get(base + "/download")
    def download(source: str):
        package = store(source)
        return FileResponse(package.path, media_type="text/html", filename=package.path.name)
