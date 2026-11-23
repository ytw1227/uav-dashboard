"""Small synthetic contract fixtures, explicitly unrelated to research performance."""
from pathlib import Path
import hashlib,json,sqlite3,zlib,os,uuid
from dashboard_contract.definitions import metric_definition,observation_definitions,head_definitions
from dashboard_contract.validation import canonical_hash

def make_bundle(output,number=1,completed_at=None,mission=False,definition_version=None,revision=None):
    root=Path(output);run_id=f"synthetic_run_{number:04d}";rev=revision or "synthetic_revision_0001"
    path=root/"bundles"/run_id/rev;path.mkdir(parents=True,exist_ok=True)
    fleet=[{"id":f"uav-{i}","role":"search" if i<2 else "relay"} for i in range(3)]
    scene={"map_id":f"synthetic-map-{number}","map_hash":f"synthetic-map-{number}","bounds":[-80,-30,120,50],
           "coordinate_system":{"type":"local_metric","unit":"m","ground_z_m":0},
           "buildings":[{"id":"fixture-building","geometry_local_m":{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,10],[0,10],[0,0]]]},"height_m":None,"height_source":"unrecorded"}],
           "fixture":True}
    (path/"scene.json").write_text(json.dumps(scene),encoding="utf-8")
    con=sqlite3.connect(path/"data.sqlite")
    con.executescript("""CREATE TABLE positions(time_s REAL PRIMARY KEY,payload TEXT);
CREATE TABLE rf_frames(time_s REAL PRIMARY KEY,interval_start_s REAL,interval_end_s REAL,payload TEXT);
CREATE TABLE observations(time_s REAL,observer TEXT,peer TEXT,payload BLOB,PRIMARY KEY(time_s,observer,peer));
CREATE TABLE decisions(time_s REAL PRIMARY KEY,payload TEXT);
CREATE TABLE events(event_id TEXT PRIMARY KEY,start_s REAL,end_s REAL,type TEXT,payload TEXT);
CREATE TABLE metrics(metric_id TEXT PRIMARY KEY,payload TEXT);""")
    for t in (0,3,6):
        p={"sample_time_s":t,"uavs":[dict(u,x=-60+i*20+t,y=i*10,z=50) for i,u in enumerate(fleet)]}
        con.execute("INSERT INTO positions VALUES(?,?)",(t,json.dumps(p)))
    for r in range(12):
        start=r*.5;end=start+.5
        payload={"interval_start_s":start,"interval_end_s":end,"available_at_s":end,
          "links":[{"id":"uav-0--uav-1","source":"uav-0","target":"uav-1","available":r<4}],
          "components":[["uav-0","uav-1"],["uav-2"]] if r<4 else [["uav-0"],["uav-1"],["uav-2"]],
          "metrics":{"search_connected":int(r<4),"generated_packets":r+1,"delivered_packets":r+1,"pdr_so_far":1}}
        con.execute("INSERT INTO rf_frames VALUES(?,?,?,?)",(end,start,end,json.dumps(payload)))
        obs={"observed_at_s":end,"available_at_s":end,"observer_id":"uav-2","peer_id":"uav-0",
             "values":{"last_ack_sinr_db":0 if r==0 else None},"valid":{"last_ack_sinr_db":r==0},"ages":{"last_ack_sinr_db":0 if r==0 else None}}
        con.execute("INSERT INTO observations VALUES(?,?,?,?)",(end,"uav-2","uav-0",zlib.compress(json.dumps(obs).encode())))
    for t in (0,3):
        payload={"decision_time_s":t,"actions":[{"uav_id":"uav-2","decision_time_s":t,"interval_start_s":t,"interval_end_s":t+3,"requested_correction_xy_m":[0,0],
          "applied_correction_xy_m":[0,0],"actual_motion_xy_m":[3,0],"actual_motion_available_at_s":t+3}],
          "predictions":[{"observer_id":"uav-2","peer_id":"uav-0","generated_at_s":t,
              "head_id":"risk_5s","value":0,"supported":True,"target_start_s":t,"target_end_s":t+5}],
          "posthoc":{"labels":"fixture only"}}
        con.execute("INSERT INTO decisions VALUES(?,?)",(t,json.dumps(payload)))
    event={"id":"outage_fixture","event_id":"outage_fixture","type":"search_outage","start_s":2,"end_s":None,"right_censored":True,
           "affected_uav_ids":["uav-0","uav-1"],"definition_id":"fixture-outage-1","fixture":True}
    con.execute("INSERT INTO events VALUES(?,?,?,?,?)",("outage_fixture",2,None,"search_outage",json.dumps(event)))
    final={"pdr":1.0,"generated_packets":12,"delivered_packets":12,"mission_completed":mission,"outage_total_s":4}
    defs={k:metric_definition(k) for k in final}
    if definition_version:defs["pdr"]["version"]=definition_version;defs["pdr"]["definition_hash"]=canonical_hash(defs["pdr"])
    for key,value in final.items():con.execute("INSERT INTO metrics VALUES(?,?)",(key,json.dumps({"metric_id":key,"key":key,"value":value,"definition":defs[key]})))
    con.commit();con.close()
    m=dict(schema_version="1.0.0",run_id=run_id,bundle_revision=rev,source_key=f"synthetic_key_{number:04d}",
      source_identity={"fixture":True,"number":number},source_fingerprint="synthetic",adapter_id="synthetic-fixture",
      adapter_version="1",artifact_kind="episode",capabilities={"trajectory":True,"summary":True,"rf_observations":True},
      stage_status="completed",export_status="ready",started_at=None,completed_at=completed_at,
      completion_time_status="recorded" if completed_at else "unknown",research_version="synthetic-fixture-only",
      model_id="synthetic-model",config_hash="synthetic-config",source_refs=[{"path":"fixture-only"}],
      artifacts={},metric_definitions=defs,observation_definitions=observation_definitions(),head_definitions=head_definitions(),event_definitions={"fixture-outage-1":{"fixture":True}},clock_definitions={},validation_report={"counts":{"positions":3}},
      map_hash=scene["map_hash"],map_id=scene["map_id"],scene_ref="scene.json",fleet=fleet,simulation_end_s=6,clocks={},
      final_metrics=final,region="SYNTHETIC STRUCTURE TEST",mode="fixture",seed=number,kind="fixture",direction="fixture",
      split="fixture",mission_completed=mission,fixture=True)
    seal(path,m)
    return path,m

def seal(path,manifest):
    for name in ("scene.json","data.sqlite"):
        content=(path/name).read_bytes()
        manifest["artifacts"][name]={"size_bytes":len(content),"sha256":hashlib.sha256(content).hexdigest()}
    raw=json.dumps(manifest,ensure_ascii=False).encode()
    (path/"manifest.json").write_bytes(raw)
    ready=json.dumps({"manifest_sha256":hashlib.sha256(raw).hexdigest()})
    temp=path/("READY."+uuid.uuid4().hex+".tmp");temp.write_text(ready);os.replace(temp,path/"READY")
