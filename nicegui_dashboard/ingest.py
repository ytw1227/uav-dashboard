"""Run the pinned Nov 13 demonstration and publish completed flights to the local service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from .managed_service import SERVICE_PROJECT

BUNDLE_FILES = ("manifest.json", "scene.json", "data.sqlite", "READY")


def utc():
    return datetime.now(timezone.utc).isoformat()


def report(stage, **fields):
    print(json.dumps({"stage": stage, **fields}, ensure_ascii=True), flush=True)


@contextmanager
def store_lock(store, timeout=180):
    """Use the original registry's OS lock; the running service remains the only scanner."""
    deadline = time.monotonic() + timeout
    while True:
        with store._output_scan_lock() as acquired:
            if acquired:
                yield
                return
        if time.monotonic() >= deadline:
            raise TimeoutError("The destination registry is busy. Retry using the same batch.")
        time.sleep(.25)


def publish(source, store):
    """Expose the four verified files in one rename. Never register rows directly."""
    from dashboard_contract.validation import confined, read_json, sha256, validate_bundle, validate_manifest

    began = time.monotonic()
    source = Path(source).resolve()
    manifest = validate_manifest(read_json(confined(source, "manifest.json")))
    rid, rev = manifest["run_id"], manifest["bundle_revision"]
    expected_hash = sha256(confined(source, "manifest.json"))
    if read_json(confined(source, "READY"), 4096).get("manifest_sha256") != expected_hash:
        raise ValueError("READY and manifest disagree")
    if source.parent.name != rid or source.name != rev:
        raise ValueError("The source directory and its immutable identity disagree")

    with store_lock(store):
        target = confined(store.output, Path("bundles") / rid / rev, must_exist=False)
        if target.exists():
            validate_bundle(target)
            if sha256(target / "manifest.json") != expected_hash:
                raise ValueError("An existing revision has different content")
            if target != source:
                validate_bundle(source)
            outcome = "already_published"
        else:
            inputs = [confined(source, name) for name in BUNDLE_FILES]
            byte_count = sum(path.stat().st_size for path in inputs)
            if store._output_usage() + byte_count > store.max_output_bytes:
                raise ValueError("storage_quota_exceeded: completed source retained; retry the same batch")
            if shutil.disk_usage(store.output).free < byte_count:
                raise ValueError("insufficient_free_space: completed source retained")
            staging = confined(store.output, "staging", must_exist=False)
            staging.mkdir(parents=True, exist_ok=True)
            # On a handled copy, hash or budget failure only this unpublished copy
            # is discarded. The committed producer result remains available for retry.
            with TemporaryDirectory(dir=staging, prefix="handoff-") as private:
                stage = Path(private)
                for path in inputs:
                    shutil.copyfile(path, stage / path.name)
                validate_bundle(stage)
                if sha256(stage / "manifest.json") != expected_hash:
                    raise ValueError("Source revision changed during publication")
                if store._output_usage() > store.max_output_bytes:
                    raise ValueError("storage_quota_exceeded: completed source retained")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(stage, target)
                outcome = "published"
        published_at = utc()
    result = dict(run_id=rid, revision=rev, outcome=outcome, published_at=published_at,
                  handoff_s=round(time.monotonic() - began, 3), completed_at=manifest["completed_at"],
                  exported_at=manifest.get("exported_at"), mission_status=manifest.get("mission_status"),
                  exporter=manifest.get("exporter_version"))
    report("bundle_handoff", **result)
    return result


def load_store(project, destination, quota_gib):
    # Reuse its lock and output-budget functions. Do not start another scanner.
    sys.path.insert(0, str(project))
    from dashboard_service.registry import Registry

    return Registry(project, destination, scan_raw=False, max_output_gib=quota_gib)


@contextmanager
def capture_lock(core, batch, region):
    """Serialize two producer commands targeting the same native NPZ/JSON pair."""
    from dashboard_service.registry import Registry
    from types import SimpleNamespace

    location = core / "code/outputs/dashboard_demo" / ("nicegui_" + batch) / ".locks" / region
    location.mkdir(parents=True, exist_ok=True)
    # This lock has no registry/catalog. Share the same stable OS lock implementation.
    with Registry._output_scan_lock(SimpleNamespace(output=location)) as acquired:
        if not acquired:
            raise RuntimeError("Another producer owns this batch and region. Retry after it finishes.")
        yield


def capture(core, store, region, batch):
    maps = json.loads((core / "dashboard_execution/selected_maps.json").read_text(encoding="utf-8"))
    if region not in {row["key"] for row in maps["maps"]} or not re.fullmatch(r"[A-Za-z0-9_-]+", region):
        raise ValueError("Choose a region key from the pinned selected_maps.json")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", batch):
        raise ValueError("Batch must use 1 to 64 letters, digits, underscores or hyphens")
    sys.path.insert(0, str(core / "dashboard_execution"))
    import run_core_pair_fixed as recorder

    with capture_lock(core, batch, region):
        recorder.APPLIED_BATCH = f"nicegui_{batch}/applied"
        recorder.NOMINAL_BATCH = f"nicegui_{batch}/nominal"
        recorder.setup()
        from dashboard_export.final_v1 import export_episode
        identity = recorder.identity()
        applied = None
        published = []
        for arm in ("v21", "nominal"):
            report("native_record", region=region, arm=arm, batch=batch, at=utc())
            result = recorder.record(region, arm, identity, export=False, paired=applied)
            export_began = time.monotonic()
            report("raw_committed", arm=arm, at=utc(), prefix=result["raw_prefix"])
            exported = export_episode(core, result["raw_prefix"], core / "dashboard_data")
            if arm == "v21":
                applied = {"run_id": exported["run_id"], "revision": exported["bundle_revision"]}
            source = core / "dashboard_data/bundles" / exported["run_id"] / exported["bundle_revision"]
            report("native_ready", arm=arm, at=utc(), export_s=round(time.monotonic()-export_began, 3), source=str(source))
            published.append(publish(source, store))
        return published


def confirm_listing(reader, published, timeout):
    from .service import ServiceReadError

    pending = {(item["run_id"], item["revision"]) for item in published}
    deadline = time.monotonic() + timeout
    error = None
    while True:
        try:
            catalog = reader.runs()
            error = None
            keys = {(row["run_id"], row["bundle_revision"]) for row in catalog["runs"]}
            for rid, rev in sorted(pending & keys):
                report("registration_confirmed", run_id=rid, revision=rev,
                       generation=catalog["generation"], observed_at=utc())
            pending -= keys
            if not pending:
                return
        except ServiceReadError as exc:
            error = exc
        if time.monotonic() >= deadline:
            raise TimeoutError(f"READY published; the configured service has not confirmed {sorted(pending)}. "
                               "Check its output root and registry status; retry using the same batch.") from error
        time.sleep(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run or resume a real applied/nominal pair and hand it off")
    run.add_argument("region")
    run.add_argument("--batch", required=True, help="Reuse this name to retry without re-running committed flights")
    run.add_argument("--research-python", type=Path, help="Defaults to the pinned research environment")
    publish_cmd = sub.add_parser("publish", help="Retry handoff of an already exported immutable bundle")
    publish_cmd.add_argument("bundle", type=Path)
    for subparser in (run, publish_cmd):
        subparser.add_argument("--core", type=Path,
                               help="Existing research runtime for run, or service project for publish")
        subparser.add_argument("--output-root", type=Path, required=True, help="The same output folder as the local result service")
        subparser.add_argument("--service-url", help="Read the connected local service's current output quota")
        subparser.add_argument("--wait-seconds", type=float, default=120, help="Time to wait for confirmation from --service-url")
        subparser.add_argument("--max-output-gib", type=float, help="Optional tighter limit; offline default is 4 GiB")
    args = parser.parse_args(argv)
    destination = args.output_root.resolve()
    if args.command == "run":
        selected = args.core or os.getenv("UAV_RESEARCH_CORE")
        if not selected:
            parser.error("Set --core or UAV_RESEARCH_CORE to your existing Nov 13 research runtime")
        core = Path(selected).resolve()
        if not (core / "dashboard_execution/run_core_pair_fixed.py").is_file():
            parser.error("The pinned Nov 13 runtime with dashboard_execution is required")
        research_python = args.research_python
        if research_python is None:
            metadata_file = core.parent / "runtime_environment.json"
            if metadata_file.is_file():
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                research_python = Path(metadata["executable"])
        if research_python is None or not research_python.is_file():
            parser.error("Set --research-python to the existing simulator environment")
        if research_python.resolve() != Path(sys.executable).resolve():
            invocation = list(sys.argv[1:] if argv is None else argv)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
            return subprocess.call([str(research_python), "-B", "-m", "nicegui_dashboard.ingest", *invocation],
                                   cwd=Path.cwd(), env=env)
    else:
        core = (args.core or SERVICE_PROJECT).resolve()
        if not (core / "dashboard_service/registry.py").is_file():
            parser.error("Set --core or UAV_SERVICE_PROJECT to an existing registry service project")
    limit = args.max_output_gib
    if not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
        parser.error("--wait-seconds must be finite and nonnegative")
    if limit is not None and (not math.isfinite(limit) or limit <= 0):
        parser.error("--max-output-gib must be positive and finite")
    if args.service_url:
        from .service import ServiceReader
        reader = ServiceReader(args.service_url)
        advertised = reader.status()["storage"]["max_bytes"] / 1024**3
        limit = advertised if limit is None else min(limit, advertised)
    store = load_store(core, destination, 4 if limit is None else limit)
    if args.command == "run":
        published = capture(core, store, args.region, args.batch)
    else:
        published = [publish(args.bundle, store)]
    if args.service_url:
        confirm_listing(reader, published, args.wait_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
