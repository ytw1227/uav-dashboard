"""Offline browser integration with a synthetic result and a simulated job queue."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nicegui import app

from nicegui_dashboard.__main__ import main as dashboard
from nicegui_dashboard.managed_service import SERVICE_PROJECT
from nicegui_dashboard import team_region_view, team_update
from nicegui_dashboard.portable import PortableDashboard
from nicegui_dashboard.__main__ import CODECS
from nicegui_dashboard.team_bundle import METRICS, SCHEMA, packed
from nicegui_dashboard.tests.shared_data import shared_files


def make_team_flight(store, sha):
    """Use the real static Bundang scene and a twelve-second synthetic flight."""
    from dashboard_contract.validation import validate_bundle
    from dashboard_tests.fixtures import seal
    manifest = dict(schema_version="1.0.0", run_id="team-synthetic-browser-flight",
                    bundle_revision="synthetic_revision_0001", adapter_version="1", artifact_kind="episode",
                    source_identity={"fixture":True}, source_fingerprint="synthetic", source_key="synthetic_test",
                    source_refs=[{"path":"fixture-only"}], capabilities={"trajectory":True,"summary":True},
                    stage_status="completed", export_status="ready", started_at=None,
                    completed_at="2026-01-01T00:00:00Z", completion_time_status="recorded",
                    model_id="synthetic-model", config_hash="synthetic-config", artifacts={},
                    clock_definitions={}, scene_ref="scene.json", simulation_end_s=12., fixture=True)
    bundle = store / "bundles" / manifest["run_id"] / manifest["bundle_revision"]
    bundle.mkdir(parents=True)
    with PortableDashboard(shared_files()["B"], codec_dir=CODECS) as package:
        rid = package.catalog["scope"]["flight_pairs"]["bundang"]["v21"]
        scene = package.read_chunk(package.catalog["entries"][rid]["scene_chunk"])
    (bundle / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    fleet = [dict(id=f"uav-{i}", role=role) for i,role in enumerate(("search","search","relay"))]
    ids = [u["id"] for u in fleet]
    final = dict(pdr=.75, ctr=5/6, timely_pdr=.5, persistent_onsets=1,
                 persistent_cut_seconds=2., generated_packets=24, delivered_packets=18,
                 duration_s=12., mission_completed=True)
    definitions = {k: dict(id="fixture."+k, key=k, label=label, unit=unit, aggregation=formula)
                   for k, (label, unit, formula) in METRICS.items()}
    clocks = dict(motion_dt_s=.1, rf_dt_s=.5, decision_dt_s=.5, position_sample_dt_s=.5)
    manifest.update(region="bundang", map_id="bundang", map_hash=scene["map_hash"],
                    adapter_id="team-halfsec", mode="v21_halfsec", team_commit=sha, research_version=sha,
                    controller="synthetic-halfsec-policy", model_sha256="c"*64,
                    decision_interval_s=.5, seed=2026110701, kind="continuous", direction="east_west",
                    split="external_demonstration", mission_completed=True, clocks=clocks, fleet=fleet,
                    final_metrics=final, metric_definitions=definitions,
                    head_definitions={"fleet_onset_probability":{"scope":"next_persistent_search_network_outage",
                                      "window_s":[3,7], "window_bounds":"[)", "minimum_outage_s":1}},
                    event_definitions={"search_outage": "fixture search outage",
                                       "persistent_search_outage": "fixture persistent outage of at least one second"},
                    observation_definitions={"beacon_sinr_db": {"unit": "dB"}})
    with closing(sqlite3.connect(bundle / "data.sqlite")) as db, db:
        db.executescript(SCHEMA)
        for step in range(25):
            t = step * .5
            uavs = [dict(u, x=-60 + 15*i + t, y=12*i, z=45) for i, u in enumerate(fleet)]
            db.execute("INSERT INTO positions VALUES(?,?)", (t, packed(dict(sample_time_s=t, available_at_s=t, uavs=uavs))))
            if step == 24:
                break
            connected = not 2 <= t < 4
            groups = [ids] if connected else [[ids[0], ids[2]], [ids[1]]]
            links = [dict(id=f"link-{i}-{j}", source=ids[i], target=ids[j], directed=False,
                          available=connected or j==2 and i==0) for i, j in ((0,1),(0,2),(1,2))]
            frame = dict(interval_start_s=t, interval_end_s=t+.5, available_at_s=t+.5,
                         links=links, components=groups, metrics=dict(search_connected=int(connected),
                         network_margin_db=10. if connected else -2., generated_packets=step+1,
                         delivered_packets=(step+1)*3//4, pdr_so_far=((step+1)*3//4)/(step+1)))
            db.execute("INSERT INTO rf_frames VALUES(?,?,?,?)", (t+.5,t,t+.5,packed(frame)))
            decision = dict(decision_time_s=t, available_at_s=t, actions=[], predictions=[], fleet_score=.2,
                            support_count=1, pre_action_connected=(not 2 <= t-.5 < 4) if t else None,
                            fleet_onset_probability=.718 if t != 1.5 else None)
            db.execute("INSERT INTO decisions VALUES(?,?)", (t,packed(decision)))
            for observer, peer in ((ids[0],ids[1]),(ids[1],ids[0]),(ids[2],ids[0])):
                obs = dict(observed_at_s=t, available_at_s=t, observer_id=observer, peer_id=peer,
                           values={"beacon_sinr_db":13.}, valid={"beacon_sinr_db":True}, ages={"beacon_sinr_db":0.})
                db.execute("INSERT INTO observations VALUES(?,?,?,?)", (t,observer,peer,packed(obs)))
        for kind in ("search_outage","persistent_search_outage"):
            event = dict(id=kind+"-000000", type=kind, definition_id=kind, start_s=2., end_s=4.,
                         duration_s=2., temporal_kind="interval", related_uav_ids=ids[:2],
                         left_censored=False, right_censored=False, counted_as_new_onset=True)
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event["id"],2.,4.,kind,packed(event)))
        for k, value in final.items():
            d = definitions[k]
            db.execute("INSERT INTO metrics VALUES(?,?)",(d["id"],json.dumps(dict(metric_id=d["id"],key=k,value=value,definition=d))))
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("positions", "rf_frames", "decisions", "observations", "events", "metrics")}
        manifest["validation_report"] = dict(counts=counts)
    seal(bundle, manifest)
    return validate_bundle(bundle)


def main():
    sys.path.insert(0, str(SERVICE_PROJECT))

    with TemporaryDirectory(prefix="team-region-browser-") as scratch:
        store = Path(scratch) / "results"
        sha = "a" * 40
        manifest = make_team_flight(store, sha)
        checkout = {"commit":sha, "assets":True, "starts":0}
        result = {"region":"bundang", "sha":sha, "finished_at":"2026-01-01T00:00:00Z",
                  "flights":{"v21":{"run_id":manifest["run_id"], "revision":manifest["bundle_revision"]}}}

        class Queue:
            def __init__(self, output_root, *, max_output_gib):
                self.output_root = Path(output_root)
                self.published = False
                self.work = None
                self.invocations = []

            def batch(self):
                return self.work

            def coverage(self, commit, *, refresh=False):
                from nicegui_dashboard.library_view import geography
                assert refresh and commit == checkout["commit"]
                if not checkout["assets"]:
                    raise ValueError("테스트 지도 자료 필요")
                return {r["region"]: dict(eligible=r["region"] != "sangam_dmc", reason="검증용 배제 지역")
                        for r in geography("B")}

            def current(self, region):
                return result if self.published and region == "bundang" else None

            def history(self, region):
                return [result] if self.current(region) else []

            def start_many(self, regions, commit):
                assert commit == checkout["commit"]
                checkout["starts"] += 1
                self.invocations.append(regions)
                if len(self.invocations) == 3:
                    expected = {i["region"] for i in self.work["items"] if i["state"] == "failed"}
                    assert len(regions) == 49 and set(regions) == expected
                    self.work = dict(sha=commit, total=49, queued=49, running=0, active=True,
                                     region=None, completed=0, failed=0, unavailable=0,
                                     items=[dict(region=r, state="queued") for r in regions])
                    return
                if len(regions) == 1:
                    assert regions == ["bundang"]
                    self.published = True
                    state = "completed"
                else:
                    from nicegui_dashboard.region_selection import region_selection
                    assert len(regions) == len(set(regions)) == 50
                    assert set(regions) == set(region_selection()["selected_regions"])
                    state = "failed"
                self.work = dict(sha=commit, total=len(regions), queued=0, running=0,
                                 active=False, region=None, completed=0, failed=0)
                self.work[state] = len(regions)
                self.work["items"] = [dict(region=r, state=state, message="브라우저 검증용") for r in regions]
                if len(regions) == 50:
                    self.work["failed"] = 49
                    self.work["unavailable"] = 1
                    next(i for i in self.work["items"] if i["region"] == "sangam_dmc")["state"] = "unavailable"

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        result_code = 1

        @app.post("/qa/external-checkout")
        def change_checkout():
            checkout.update(commit="b"*40, assets=False)
            return checkout

        @app.post("/qa/repair-assets")
        def repair_assets():
            checkout["assets"] = True
            return checkout

        @app.post("/qa/complete-comparison")
        def complete_comparison():
            from result_service.dashboard_contract.validation import canonical_hash
            from nicegui_dashboard.team_baseline_record import comparison_path, pin_fields
            from nicegui_dashboard.team_capture import write_result

            rid, rev = manifest["run_id"], manifest["bundle_revision"]
            bundle = store / "bundles" / rid / rev
            units = dict(search_connected_mean="ratio", pdr="ratio", persistent_cut_seconds="s",
                         persistent_onsets="count", information_shared_fraction="ratio")
            definitions = {k: dict(unit=v, definition_hash="test-definition-" + k) for k, v in units.items()}
            applied = dict(manifest, mode="v21", pair_key=f"{rid}/{rev}", metric_definitions=definitions,
                           raw_metadata={"persistent_cut_intervals": [[2, 4]]},
                           final_metrics=dict(manifest["final_metrics"], search_connected_mean=5/6,
                                              information_shared_fraction=.99))
            nominal = dict(applied, mode="nominal", run_id="nominal-synthetic-browser-flight",
                           controller="nominal_no_relocation", simulation_end_s=15,
                           raw_metadata={"persistent_cut_intervals": [[0, 1], [3, 6], [14, 15]]},
                           final_metrics=dict(applied["final_metrics"], pdr=.70, persistent_onsets=2,
                                              persistent_cut_seconds=5., search_connected_mean=2/3))
            record = dict(pin_fields(bundle, manifest), nominal=nominal, applied=applied)
            write_result(comparison_path(bundle), dict(record, payload_sha256=canonical_hash(record)))
            return {"completed": True}

        def current_source(_):
            return {"commit":checkout["commit"]}

        def sync_source(path):
            time.sleep(0.7)
            return {**current_source(path), "changed":False}

        @app.on_startup
        async def verify():
            nonlocal result_code
            try:
                script = Path(__file__).with_name("team_run_browser.cjs")
                proc = await asyncio.to_thread(subprocess.run, ["node", str(script), f"http://127.0.0.1:{port}"], timeout=180)
                result_code = proc.returncode
            finally:
                app.shutdown()

        from nicegui_dashboard import team_source_view
        with (patch.object(team_update, "TeamUpdates", Queue),
              patch.object(team_source_view, "source_status", current_source),
              patch.object(team_source_view, "sync_source", sync_source),
              patch.object(team_region_view, "source_status", current_source),
              patch.object(sys, "argv", ["dashboard","app","--port",str(port),"--data-root",str(store)])):
            dashboard()
        return result_code


if __name__ == "__main__":
    sys.exit(main())
