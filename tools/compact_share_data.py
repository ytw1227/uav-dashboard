#!/usr/bin/env python3
"""Create a selected-region offline HTML without changing recorded data.

The source must be a separate, trusted UAV offline HTML. Navigation indexes are
projected with the application's existing region selection. Referenced data
blocks, runtime scripts, CSS, and HTML are copied byte for byte. Only the packed
catalog is regenerated. Each retained data block is checked for dependencies and
every copied script is verified against its source SHA-256 before installation.

Example (run from the project directory)::

    python tools/compact_share_data.py --source /path/to/original.html \
        --output data/UAV_대시보드_v2.html --report /path/to/compact_report.json
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from nicegui_dashboard.portable import PortableDashboard  # noqa: E402
from nicegui_dashboard import region_selection as selection_module  # noqa: E402


def check(condition, message):
    if not condition:
        raise ValueError(message)


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def value_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_values(value):
    """Yield strings from keys and values without copying the object tree."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def json_references(value, block_ids):
    return {text for text in walk_values(value) if text in block_ids}


def column_references(raw, block_ids):
    """Validate a UAVC1/2 tree and inspect strings without expanding numbers.

    The lossless format stores strings verbatim and numeric columns as fixed
    width byte streams. Reading its structure is sufficient to find all embedded
    chunk identifiers while avoiding millions of Python numeric/list objects.
    Data bytes are never rewritten by this function.
    """
    check(raw[:5] in (b"UAVC1", b"UAVC2"), "Unsupported lossless column header")
    data = memoryview(raw)
    at = 5
    found = set()

    def take(size):
        nonlocal at
        check(size >= 0 and at + size <= len(data), "Truncated lossless column")
        result = data[at:at + size]
        at += size
        return result

    def integer():
        value = 0
        shift = 0
        while True:
            digit = take(1)[0]
            value |= (digit & 127) << shift
            if digit < 128:
                return value
            shift += 7
            check(shift <= 49, "Invalid lossless varint")

    def string():
        value = bytes(take(integer())).decode("utf-8", errors="surrogatepass")
        if value in block_ids:
            found.add(value)

    def column():
        count = integer()
        if not count:
            return count
        kind = take(1)[0]
        if kind in (0, 1):
            pass
        elif kind == 2:
            take((count + 7) // 8)
        elif kind == 3:
            width = take(1)[0]
            check(width in (0, 4, 8), "Invalid lossless numeric width")
            take(8 if width == 0 else width * count)
        elif kind == 4:
            words = integer()
            check(words > 0, "Empty lossless string dictionary")
            for _ in range(words):
                string()
            if words != 1:
                for _ in range(count):
                    check(integer() < words, "Invalid lossless string index")
        elif kind == 5:
            check(column() == count, "Invalid lossless array length column")
            column()
        elif kind == 6:
            keys = integer()
            for _ in range(keys):
                string()
            shapes = integer()
            check(shapes > 0, "Empty lossless shape dictionary")
            for _ in range(shapes):
                for _ in range(integer()):
                    check(integer() < keys, "Invalid lossless shape key")
            if shapes != 1:
                for _ in range(count):
                    check(integer() < shapes, "Invalid lossless shape index")
            for _ in range(keys):
                check(column() == count, "Invalid lossless object column length")
        elif kind == 7:
            kinds = set(take(count))
            check(all(0 <= item <= 6 for item in kinds), "Invalid mixed lossless kind")
            # Each subtype is encoded as its own complete column.
            total = sum(column() for _ in sorted(kinds))
            check(total == count, "Invalid mixed lossless column lengths")
        else:
            raise ValueError("Unknown lossless column type")
        return count

    check(column() == 1 and at == len(data), "Invalid lossless root or trailing data")
    data.release()
    return found


def require_catalog_links(catalog, block_ids):
    """Check all link fields used by offline playback, including both modes."""
    for rid, entry in catalog["entries"].items():
        for key in ("scene_chunk", "overview_chunk", "events_chunk"):
            check(entry[key] in block_ids, f"Missing {rid}/{key}: {entry[key]}")
        for pair, chunk in entry.get("observations", {}).items():
            check(chunk in block_ids, f"Missing observation {rid}/{pair}: {chunk}")
    for group in catalog.get("dashboard_analysis", {}).values():
        for descriptor in group.values():
            for key, value in descriptor.items():
                if key == "chunk" or key.endswith("_chunk"):
                    check(value in block_ids, f"Missing analysis reference {key}: {value}")
    for region, descriptor in catalog.get("visualization", {}).get("maps", {}).items():
        check(descriptor["map_chunk"] in block_ids, f"Missing visual map: {region}")


def dependency_closure(source, initial):
    """Inspect each retained payload once; follow any additional data links."""
    block_ids = set(source._blocks)
    kept = set(initial)
    queue = deque(sorted(initial))
    checked = set()
    extra = set()
    kinds = {}
    uncompressed_bytes = 0
    while queue:
        key = queue.popleft()
        if key in checked:
            continue
        block = source._blocks[key]
        mime = block.attrs.get("type")
        if mime == "application/octet-stream":
            codec, packed = source.packed_chunk(key)
            raw = gzip.decompress(packed)  # also verifies gzip CRC and length
            uncompressed_bytes += len(raw)
            if codec == "lossless-columns":
                refs = column_references(raw, block_ids)
            elif codec == "json":
                refs = json_references(json.loads(raw), block_ids)
            elif codec == "metadata-dag":
                refs = json_references(source._decode_metadata(json.loads(raw)), block_ids)
            else:
                raise ValueError(f"Unknown data codec {codec!r} in {key}")
            del raw, packed
            kinds[codec] = kinds.get(codec, 0) + 1
        elif mime == "application/json":
            refs = json_references(source.read_chunk(key), block_ids)
            kinds["plain-json"] = kinds.get("plain-json", 0) + 1
        else:
            raise ValueError(f"Catalog refers to executable code: {key}")
        checked.add(key)
        for ref in sorted(refs - kept):
            check(ref != "portable-catalog", "Data payload recursively refers to catalog")
            kept.add(ref)
            extra.add(ref)
            queue.append(ref)
        if len(checked) % 1000 == 0:
            print(json.dumps({"phase": "check_dependencies", "checked": len(checked),
                              "queued": len(queue)}), flush=True)
    return kept, {"checked_data_chunks": len(checked), "codecs": kinds,
                  "uncompressed_bytes_scanned": uncompressed_bytes,
                  "transitive_chunks_added": sorted(extra)}


def copy_span(output, mapping, start, end):
    view = memoryview(mapping)[start:end]
    try:
        output.write(view)
    finally:
        view.release()


def shell_digest(source):
    """Hash bytes outside identified script blocks (CSS, markup, boot loader)."""
    digest = hashlib.sha256()
    cursor = 0
    for block in sorted(source._blocks.values(), key=lambda item: item.start):
        digest.update(source._file[cursor:block.start])
        cursor = block.end
    digest.update(source._file[cursor:])
    return digest.hexdigest()


def build_catalog(source_catalog, selection, source_sha256):
    result = selection_module.presentation_catalog(source_catalog, "B")
    scope = dict(result["scope"])
    count = len(scope["maps"])
    original_count = len(source_catalog["scope"]["maps"])
    scope["dashboard_selection"] = {
        **scope.get("dashboard_selection", {}),
        "available_maps": count,
        "source_maps": original_count,
        "selected_maps": count,
        "original_recordings_preserved": False,
        "selected_recordings_preserved": True,
    }
    scope["distribution_compaction"] = {
        "version": 1,
        "selection_id": selection["selection_id"],
        "source_sha256": source_sha256,
        "source_maps": original_count,
        "packaged_maps": count,
        "excluded_regions": [r for r in source_catalog["scope"]["maps"]
                             if r not in set(scope["maps"])],
        "retained_chunk_bytes_unchanged": True,
    }
    if "urban_selection" in scope:
        original = source_catalog["scope"]["urban_selection"]
        scope["source_urban_selection"] = original
        scope["urban_selection"] = {**original, "included_maps": count,
            "omitted_maps": list(dict.fromkeys([*original.get("omitted_maps", []),
                                                *scope["distribution_compaction"]["excluded_regions"]]))}
    return {**result, "scope": scope}


def verify(source, target, expected, copied_ids, selection):
    check(target.catalog == expected, "Catalog failed lossless round-trip")
    selected = set(selection["selected_regions"])
    check(set(target.catalog["scope"]["maps"]) == selected, "Selected map set changed")
    check(set(target.catalog["scope"]["flight_pairs"]) == selected, "Pair region set changed")
    check(set(target.catalog["entries"]) == {rid for pair in target.catalog["scope"]["flight_pairs"].values()
                                           for rid in pair.values()}, "Incomplete flight pair index")
    check(len(target.catalog["entries"]) == 2 * len(selected), "Expected two arms per region")
    require_catalog_links(target.catalog, set(target._blocks))
    source_rows = {row["run_id"]: row for row in source.catalog["runs"]}
    target_rows = {row["run_id"]: row for row in target.catalog["runs"]}
    flights = {}
    for region, pair in target.catalog["scope"]["flight_pairs"].items():
        check(set(pair) == {"nominal", "v21"}, f"Incomplete nominal/v21 pair: {region}")
        for mode, rid in pair.items():
            entry = target.catalog["entries"][rid]
            check(entry == source.catalog["entries"][rid], f"Recorded entry changed: {rid}")
            check(target_rows[rid] == source_rows[rid], f"Recorded row changed: {rid}")
            # Canonical JSON also distinguishes -0.0 from 0.0 and int from float,
            # which Python's ordinary container equality intentionally does not.
            check(value_digest(entry) == value_digest(source.catalog["entries"][rid]),
                  f"Canonical recorded entry changed: {rid}")
            check(value_digest(target_rows[rid]) == value_digest(source_rows[rid]),
                  f"Canonical recorded row changed: {rid}")
            check(entry["manifest"]["region"] == region, f"Recorded region changed: {rid}")
            flights[rid] = {"region": region, "mode": mode,
                "revision": entry["revision"], "entry_sha256": value_digest(entry),
                "manifest_sha256": value_digest(entry["manifest"]),
                "final_metrics_sha256": value_digest(entry["manifest"].get("final_metrics")),
                "row_sha256": value_digest(target_rows[rid])}
    for key, item in target.catalog.get("comparisons", {}).items():
        check(item == source.catalog["comparisons"][key], f"Comparison changed: {key}")
    for group, descriptors in target.catalog.get("dashboard_analysis", {}).items():
        for key, descriptor in descriptors.items():
            check(descriptor == source.catalog["dashboard_analysis"][group][key],
                  f"Analysis descriptor changed: {group}/{key}")
    for key, descriptor in target.catalog.get("visualization", {}).get("maps", {}).items():
        check(descriptor == source.catalog["visualization"]["maps"][key], f"Visualization changed: {key}")
    check(set(target._blocks) == set(copied_ids) | {"portable-catalog"}, "Unexpected output blocks")
    digests = {}
    for key in sorted(copied_ids):
        digest = source.block_digest(key)
        check(target.block_digest(key) == digest, f"Copied script bytes changed: {key}")
        digests[key] = digest
    source_shell = shell_digest(source)
    check(shell_digest(target) == source_shell, "HTML/CSS/bootstrap shell changed")
    return {"passed": True, "catalog_roundtrip_equal": True,
            "all_retained_entries_and_rows_equal": True,
            "all_retained_manifests_and_final_metrics_equal": True,
            "retained_entries_and_rows_canonical_sha256_equal": True,
            "comparison_and_analysis_values_equal": True,
            "all_copied_script_sha256_equal": True,
            "html_css_bootstrap_shell_equal": True,
            "shell_sha256": source_shell,
            "retained_flights": flights,
            "copied_script_sha256": digests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--selection", type=Path, default=selection_module.SELECTION_PATH)
    parser.add_argument("--codec-dir", type=Path, default=PROJECT / "nicegui_dashboard/vendor/codecs")
    parser.add_argument("--sangam", type=Path, default=PROJECT / "data/상암DMC_대시보드.html")
    args = parser.parse_args()
    source_path, output_path = args.source.resolve(), args.output.resolve()
    check(source_path != output_path, "Keep the source intact; source and output must differ")
    check(args.report.resolve() not in (source_path, output_path), "Report must be a separate file")
    selection_module.SELECTION_PATH = args.selection.resolve()
    selection_module.region_selection.cache_clear()
    selection = selection_module.region_selection()
    original_sha = file_digest(source_path)
    sangam_sha = file_digest(args.sangam) if args.sangam.is_file() else None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with PortableDashboard(source_path, codec_dir=args.codec_dir) as source:
            check(set(source.catalog["scope"]["maps"]) ==
                  set(selection["selected_regions"]) | set(selection["excluded_regions"]),
                  "Source map set does not match the selected source inventory")
            expected = build_catalog(source.catalog, selection, original_sha)
            block_ids = set(source._blocks)
            require_catalog_links(expected, block_ids)
            initial = json_references(expected, block_ids)
            keep_data, closure_report = dependency_closure(source, initial)
            # Preserve every non-packed block and the executable Plotly runtime.
            runtime = {key for key, block in source._blocks.items()
                       if block.attrs.get("type") != "application/octet-stream"}
            runtime.add("plotly-runtime")
            copied_ids = (keep_data | runtime) - {"portable-catalog"}
            removed_ids = block_ids - copied_ids - {"portable-catalog"}
            check(all(source._blocks[key].attrs.get("type") == "application/octet-stream"
                      for key in removed_ids), "Refusing to remove executable or markup blocks")
            pack_metadata = runpy.run_path(str(args.codec_dir / "metadata_pack.py"))["pack_metadata"]
            encoded_dag = pack_metadata(expected)
            raw = compact_json(encoded_dag)
            raw_bytes = len(raw)
            packed = gzip.compress(raw, compresslevel=9, mtime=0)
            catalog_body = base64.b85encode(packed).replace(b"<", b":")
            catalog_sizes = {"dag_json_bytes": raw_bytes, "gzip_bytes": len(packed),
                             "base85_bytes": len(catalog_body)}
            del encoded_dag, raw, packed
            gc.collect()
            print(json.dumps({"phase": "write", "retained_data_chunks": len(keep_data),
                              "removed_chunks": len(removed_ids)}), flush=True)
            with tempfile.NamedTemporaryFile(mode="wb", dir=output_path.parent,
                                             prefix=".compact-share-", suffix=".html", delete=False) as out:
                temporary = Path(out.name)
                cursor = 0
                for key, block in sorted(source._blocks.items(), key=lambda item: item[1].start):
                    copy_span(out, source._file, cursor, block.start)
                    if key == "portable-catalog":
                        copy_span(out, source._file, block.start, block.body_start)
                        out.write(catalog_body)
                        copy_span(out, source._file, block.body_end, block.end)
                    elif key in copied_ids:
                        copy_span(out, source._file, block.start, block.end)
                    cursor = block.end
                copy_span(out, source._file, cursor, len(source._file))
                out.flush()
                os.fsync(out.fileno())
            del catalog_body
            with PortableDashboard(temporary, codec_dir=args.codec_dir) as target:
                verification = verify(source, target, expected, copied_ids, selection)
                output_sha = target.file_digest()
                output_size = temporary.stat().st_size
            check(file_digest(source_path) == original_sha, "Source changed during compaction")
            if sangam_sha is not None:
                check(file_digest(args.sangam) == sangam_sha, "Sangam HTML changed")
            report = {
                "schema_version": 1, "tool": "tools/compact_share_data.py",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": {"path": str(source_path), "sha256": original_sha,
                           "size_bytes": source_path.stat().st_size,
                           "maps": len(source.catalog["scope"]["maps"]),
                           "flights": len(source.catalog["entries"]), "script_blocks": len(source._blocks)},
                "output": {"path": str(output_path), "sha256": output_sha,
                           "size_bytes": output_size, "maps": len(expected["scope"]["maps"]),
                           "flights": len(expected["entries"]), "script_blocks": len(copied_ids) + 1,
                           "catalog": catalog_sizes},
                "bytes_saved": source_path.stat().st_size - output_size,
                "selection": {"path": str(args.selection.resolve()), "sha256": file_digest(args.selection),
                              "selection_id": selection["selection_id"],
                              "selected_regions": expected["scope"]["maps"],
                              "excluded_regions": selection["excluded_regions"]},
                "sangam_unchanged": {"path": str(args.sangam.resolve()), "sha256": sangam_sha},
                "dependencies": closure_report,
                "removed_chunk_ids": sorted(removed_ids),
                "verification": verification,
                "limitations": ["Data and dependency validation only; this tool does not run a browser.",
                                "All runtime code is preserved, including geography label tables."]}
        os.replace(temporary, output_path)
        temporary = None
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"phase": "complete", "sha256": output_sha, "size_bytes": output_size,
                          "maps": report["output"]["maps"], "script_blocks": report["output"]["script_blocks"],
                          "verification_passed": True, "report": str(args.report.resolve())}), flush=True)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
