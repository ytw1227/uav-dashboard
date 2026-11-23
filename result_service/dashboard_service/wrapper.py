"""Foreground opt-in research wrapper followed by result-only export/registration.

Example:
  python -B -m dashboard_service.wrapper --project-root P --output-root O \
    --research-python PYTHON --command evaluate --run saved_run

The explicit wrapper invocation authorizes only the allowlisted bounded command.
It never copies parent process timestamps into historical episode metadata.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from dashboard_service.runner import JobManager


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--research-python", required=True)
    parser.add_argument("--exporter-python")
    parser.add_argument("--max-output-gib", type=float, default=4.0)
    parser.add_argument("--command", choices=("evaluate", "doctor", "verify"), required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run")
    group.add_argument("--checkpoint")
    args = parser.parse_args(argv)
    project = Path(args.project_root).resolve(strict=True)
    output = Path(args.output_root).resolve()
    if project.is_relative_to(output) or any(output.is_relative_to(project / name)
            for name in ("results", "maps", "model", "models", "solo", "cooperative", "configs", "settings", "docs")):
        parser.error("Dashboard output must be separate from research files")
    import math
    if not math.isfinite(args.max_output_gib) or args.max_output_gib <= 0:
        parser.error("--max-output-gib must be finite and positive")
    output.mkdir(parents=True, exist_ok=True)
    spec = {"command": args.command}
    if args.run:
        spec["run"] = args.run
    if args.checkpoint:
        spec["checkpoint"] = args.checkpoint
    manager = JobManager(output / "jobs", project, args.research_python, enabled=True)
    job = manager.start(spec)
    print(json.dumps({"phase": "research_started", "job": job}, ensure_ascii=False), flush=True)
    interrupted = False
    active = {"starting", "running", "stopping", "ownership_unknown"}
    while True:
        try:
            result = manager.get_job(job["job_id"])
            if result["status"] not in active:
                break
            time.sleep(.2)
        except KeyboardInterrupt:
            interrupted = True
            manager.stop(job["job_id"])
    print(json.dumps({"phase": "research_finished", "job": result}, ensure_ascii=False), flush=True)
    registry = None
    try:
        from dashboard_service.registry import Registry
        registry = Registry(project, output, export_python=args.exporter_python,
                            max_exports=100000, scan_raw=True, max_output_gib=args.max_output_gib)
        # New or changed completed sources are detected by signature. Existing
        # successful revisions do not need an expensive forced export again.
        registry.rescan()
        scanned = registry.scan_once(force=False)
        status = registry.status()
        pending = {key: value for key, value in status.get("counts", {}).items()
                   if key in {"waiting", "waiting_exporter", "waiting_quota", "error"} and value}
        outcome = "deferred" if not scanned else "partial" if pending or status.get("errors") else "finished"
        registration_incomplete = outcome != "finished"
        print(json.dumps({"phase": "registration_" + outcome, "scan": scanned,
                          "pending": pending, "error_count": len(status.get("errors", [])),
                          "deferred_reason": status.get("scan_deferred_reason"),
                          "timestamps_scope": "launcher_parent_only"}, ensure_ascii=False, default=str), flush=True)
    except Exception as error:
        print(json.dumps({"phase": "registration_failed", "error_type": type(error).__name__,
                          "message": "Research outcome retained. Run the service rescan to retry registration."},
                         ensure_ascii=False), file=sys.stderr, flush=True)
        return 2
    finally:
        if registry is not None:
            registry.close()
    if interrupted or result["status"] == "stopped":
        return 130
    if registration_incomplete and result["status"] == "succeeded":
        return 2
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
