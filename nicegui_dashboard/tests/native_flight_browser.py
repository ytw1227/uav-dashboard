"""Check a sealed current or historical flight on the real region page in an isolated registry."""
import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nicegui import app

from nicegui_dashboard.__main__ import main as dashboard
from nicegui_dashboard.ingest import load_store, publish_group
from nicegui_dashboard.managed_service import SERVICE_PROJECT
from nicegui_dashboard.team_baseline_record import comparison_path, load_comparison, nominal_bundle
from nicegui_dashboard.team_forecast_record import forecast_path, load_forecast
from nicegui_dashboard.team_replay_data import unpack
from nicegui_dashboard.team_update import TeamUpdates
from result_service.dashboard_contract.validation import validate_bundle


def browser_inputs(bundle, applied, baseline):
    rid,rev=applied["run_id"],applied["bundle_revision"]
    sidecar=forecast_path(bundle.parents[2],rid,rev)
    extra=(load_forecast(sidecar,applied,json.loads((bundle/"READY").read_bytes())["manifest_sha256"])
           if sidecar.is_file() else None)
    pair=load_comparison(bundle)
    with closing(sqlite3.connect((bundle/"data.sqlite").as_uri()+"?mode=ro&immutable=1", uri=True)) as db:
        cut = db.execute("SELECT event_id,start_s,end_s FROM events WHERE type='persistent_search_outage' "
                         "AND start_s>0 ORDER BY start_s LIMIT 1").fetchone()
        forecasts = db.execute("SELECT d.payload,f.payload FROM decisions d "
                               "JOIN rf_frames f ON f.interval_start_s=d.time_s ORDER BY d.time_s")
        forecast = None
        for index,(decision,frame) in enumerate(forecasts):
            d=unpack(decision)
            time=d["decision_time_s"]
            p=extra["probabilities"][index] if extra else d.get("fleet_onset_probability")
            before=extra["pre_action_connected"][index] if extra else d.get("pre_action_connected")
            if (2 <= time < applied["simulation_end_s"]-7 and d.get("support_count",0)>0 and before
                    and p is not None and unpack(frame)["metrics"]["search_connected"]):
                forecast=[time,p]
                break
    return dict(region=applied["region"], sha=applied["team_commit"], revision=applied["bundle_revision"],
                controller=applied["controller"], run=rid, nominal=baseline["run_id"] if baseline else None,
                metrics=[applied["final_metrics"],pair["nominal"]["final_metrics"] if pair else None], cut=cut,
                forecast=forecast,evaluation=(applied.get("regional_evaluation") or {}).get("summary"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Existing applied team bundle (nominal replay is optional)")
    parser.add_argument("--late-nominal", action="store_true", help="Register the baseline after opening the applied flight")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    nominal = nominal_bundle(bundle)
    manifest = validate_bundle(bundle)
    baseline = validate_bundle(nominal) if (nominal/"READY").is_file() else None
    if args.late_nominal and not baseline:
        parser.error("--late-nominal requires a matching sealed baseline")
    plan = browser_inputs(bundle, manifest, baseline)
    plan["lateNominal"] = args.late_nominal
    with TemporaryDirectory(prefix="uav-native-replay-qa-") as scratch:
        store_path = Path(scratch)/"results"
        store = load_store(SERVICE_PROJECT, store_path, 4)
        try:
            sources = [nominal,bundle] if baseline and not args.late_nominal else [bundle]
            pins = publish_group(sources, store)
            store.scan_once(force=True)
            for pin in pins:
                assert store.get(pin["run_id"], pin["revision"])
        finally:
            store.close()
        assert {p["run_id"] for p in pins} == {source.parent.name for source in sources}
        for src in (comparison_path(bundle),forecast_path(bundle.parents[2],plan["run"],plan["revision"])):
            if src.is_file():
                dst=store_path/src.relative_to(bundle.parents[2])
                dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(src,dst)
        updates = TeamUpdates(store_path, resume=False)
        flights={"v21":dict(run_id=plan["run"],revision=plan["revision"])}
        if baseline and not args.late_nominal:
            flights["nominal"]=dict(run_id=plan["nominal"],revision=plan["revision"])
        with updates.connection() as db:
            bid = db.execute("INSERT INTO batches(sha,source,core,python,added) VALUES(?,?,?,?,?)",
                             (plan["sha"],"read-only","read-only","read-only",manifest["completed_at"])).lastrowid
            db.execute("INSERT INTO jobs(batch,region,state,phase,message,finished_at,flights) VALUES(?,?,?,?,?,?,?)",
                       (bid, plan["region"], "completed", "published", "", manifest["completed_at"], json.dumps(flights)))
        assert updates.current(plan["region"])["sha"] == plan["sha"]
        plan_path = Path(scratch)/"expected.json"
        plan_path.write_text(json.dumps(plan),encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        result = 1

        if args.late_nominal:
            @app.post("/qa/register-nominal")
            def register_nominal():
                temporary_store = load_store(SERVICE_PROJECT, store_path, 4)
                try:
                    return publish_group([nominal], temporary_store)[0]
                finally:
                    temporary_store.close()

        @app.on_startup
        async def verify():
            nonlocal result
            try:
                proc = await asyncio.to_thread(subprocess.run,
                    ["node",str(Path(__file__).with_suffix(".cjs")),f"http://127.0.0.1:{port}/regions",str(plan_path)],
                    timeout=650)
                result = proc.returncode
            finally:
                app.shutdown()

        with patch.object(sys,"argv",["dashboard","app","--data-root",str(store_path),"--port",str(port)]):
            dashboard()
        return result


if __name__ == "__main__":
    sys.exit(main())
