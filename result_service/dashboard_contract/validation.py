"""Strict portable bundle validation without importing any research or NumPy modules."""
from pathlib import Path
from datetime import datetime
import hashlib, json, math, re, sqlite3

SCHEMA_VERSION = "1.0.0"
MAX_JSON = 32 * 1024 * 1024
MAX_DATABASE = 2 * 1024**3

class ContractError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")

def fail(code, message):
    raise ContractError(code, message)

def sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()

def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

def read_json(path, maximum=MAX_JSON):
    path = Path(path)
    if path.stat().st_size > maximum:
        fail("oversize_json", path.name)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("duplicate_json_key", key)
            result[key] = value
        return result
    try:
        with path.open(encoding="utf-8-sig") as f:
            return json.load(f, object_pairs_hook=unique, parse_constant=lambda x: fail("nonfinite_json", x))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail("invalid_json", f"{path.name}: {exc}")

def confined(root, relative, must_exist=True):
    root = Path(root).resolve()
    path = Path(relative)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        fail("path_outside_root", str(relative))
    if must_exist and not path.is_file():
        fail("missing_file", str(relative))
    return path

def _finite_tree(value, trail=""):
    if isinstance(value, float) and not math.isfinite(value):
        fail("nonfinite", trail)
    if isinstance(value, dict):
        for key, item in value.items(): _finite_tree(item, trail + "." + key)
    elif isinstance(value, list):
        for i, item in enumerate(value): _finite_tree(item, trail + f"[{i}]")

def validate_manifest(manifest):
    if not isinstance(manifest, dict): fail("invalid_manifest", "Expected object")
    version = manifest.get("schema_version", "")
    if not re.fullmatch(r"1\.\d+\.\d+", version):
        fail("unsupported_schema", str(version))
    required = ("run_id", "bundle_revision", "source_identity", "source_fingerprint",
                "source_key", "adapter_id", "adapter_version", "artifact_kind", "capabilities",
                "stage_status", "export_status", "started_at", "completed_at", "completion_time_status",
                "research_version", "model_id", "config_hash", "source_refs", "artifacts",
                "metric_definitions", "event_definitions", "clock_definitions", "validation_report")
    for key in required:
        if key not in manifest: fail("missing_manifest_field", key)
    for key in ("run_id", "bundle_revision", "source_key"):
        if not re.fullmatch(r"[a-z0-9_-]{8,80}", manifest[key]):
            fail("invalid_identifier", key)
    if manifest["export_status"] != "ready": fail("not_ready", "Export not ready")
    if manifest["artifact_kind"] == "episode":
        for key in ("map_hash", "scene_ref", "fleet", "simulation_end_s", "clocks", "final_metrics"):
            if key not in manifest: fail("missing_manifest_field", key)
        fleet = manifest["fleet"]
        ids = [row["id"] for row in fleet]
        if not ids or len(ids) != len(set(ids)): fail("invalid_fleet", "Empty/duplicate ids")
        if manifest["simulation_end_s"] <= 0: fail("invalid_duration", "Positive duration required")
    if manifest["completed_at"] is None and manifest["completion_time_status"] == "recorded":
        fail("invalid_completion_time", "Recorded completion must have a value")
    times={}
    for key in ("started_at","completed_at"):
        value=manifest[key]
        if value is None:continue
        try:
            parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
        except (ValueError,TypeError,AttributeError):fail("invalid_completion_time",key+": timezone-aware ISO-8601 required")
        times[key]=parsed
    if "started_at" in times and "completed_at" in times and times["started_at"]>times["completed_at"]:
        fail("completion_time_order","started_at must not exceed completed_at")
    _finite_tree(manifest)
    return manifest


def decode_payload(payload, maximum=1048576):
    if isinstance(payload,bytes):
        import zlib
        dec=zlib.decompressobj()
        try:
            raw=dec.decompress(payload,maximum+1)
        except zlib.error as exc: fail("invalid_compression",str(exc))
        if len(raw)>maximum or dec.unconsumed_tail or not dec.eof or dec.unused_data:
            fail("oversize_payload","Invalid or excessive compressed row")
        payload=raw.decode("utf-8")
    elif not isinstance(payload,str):fail("invalid_payload","Expected JSON text or zlib bytes")
    if len(payload.encode("utf-8"))>maximum:fail("oversize_payload","row")
    try:
        item=json.loads(payload,parse_constant=lambda x:fail("nonfinite_payload",x))
    except (json.JSONDecodeError,UnicodeError) as exc:fail("invalid_payload_json",str(exc))
    if not isinstance(item,dict):fail("invalid_payload","Object required")
    _finite_tree(item)
    return item

def validate_bundle(bundle_path, verify_hashes=True):
    from contextlib import closing
    bundle=Path(bundle_path).resolve()
    ready=read_json(confined(bundle,"READY"),4096)
    manifest_path=confined(bundle,"manifest.json")
    if ready.get("manifest_sha256")!=sha256(manifest_path):fail("manifest_hash_mismatch",bundle.name)
    manifest=validate_manifest(read_json(manifest_path))
    for name in ("scene.json","data.sqlite"):
        artifact=manifest["artifacts"].get(name)
        if not isinstance(artifact,dict):fail("missing_artifact",name)
        path=confined(bundle,name)
        maximum=MAX_DATABASE if name.endswith("sqlite") else MAX_JSON
        if path.stat().st_size>maximum:fail("oversize_artifact",name)
        if path.stat().st_size!=artifact["size_bytes"]:fail("artifact_size_mismatch",name)
        if verify_hashes and sha256(path)!=artifact["sha256"]:fail("artifact_hash_mismatch",name)
    scene=read_json(bundle/"scene.json")
    if scene.get("map_hash")!=manifest.get("map_hash"):fail("scene_map_mismatch","scene/manifest")
    bounds=scene.get("bounds",[])
    if len(bounds)!=4 or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]:fail("invalid_scene_bounds","finite ordered 4 coordinates required")
    _finite_tree(scene)
    ids={row["id"] for row in manifest["fleet"]};duration=manifest["simulation_end_s"]
    counts={};previous={};link_ids=set()
    def timestamp(value):
        if not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value) or value<0 or value>duration+1e-8:
            fail("invalid_channel_time",str(value))
    def same(a,b):
        if not isinstance(a,(float,int)) or not math.isclose(a,b,abs_tol=1e-8):fail("payload_time_mismatch",str(a)+"/"+str(b))
    uri=(bundle/"data.sqlite").as_uri()+"?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri,uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0]!="ok":fail("database_corrupt","quick_check")
        names={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required=("positions","rf_frames","observations","decisions","events","metrics")
        for table in required:
            if table not in names:fail("missing_table",table)
            counts[table]=conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            expected=manifest["validation_report"].get("counts",{}).get(table)
            if expected is not None and counts[table]!=expected:fail("row_count_mismatch",table)
        for table in ("positions","rf_frames","decisions"):
            if counts[table]==0:fail("empty_channel",table)
        for time_s,payload in conn.execute("SELECT time_s,payload FROM positions ORDER BY time_s"):
            timestamp(time_s);p=decode_payload(payload);same(p.get("sample_time_s"),time_s)
            uavs=p.get("uavs",[])
            if len(uavs)!=len(ids) or {x.get("id") for x in uavs}!=ids:fail("position_fleet_mismatch","unknown/missing/duplicate UAV")
            for u in uavs:
                for axis in ("x","y"):
                    if not isinstance(u.get(axis),(float,int)) or not math.isfinite(u[axis]):fail("invalid_position",axis)
                if not bounds[0]-1e-4<=u["x"]<=bounds[2]+1e-4 or not bounds[1]-1e-4<=u["y"]<=bounds[3]+1e-4:
                    fail("positions_outside_map",u["id"])
            previous["positions"]=time_s
        first=conn.execute("SELECT MIN(time_s) FROM positions").fetchone()[0]
        if first!=0 or previous["positions"]!=duration:fail("position_endpoints","0 and simulation_end required")
        last_end=0.
        for time_s,start,end,payload in conn.execute("SELECT time_s,interval_start_s,interval_end_s,payload FROM rf_frames ORDER BY time_s"):
            timestamp(start);timestamp(end)
            if start>=end or abs(start-last_end)>1e-8:fail("invalid_rf_interval","positive contiguous RF intervals required")
            same(time_s,end);last_end=end;p=decode_payload(payload)
            same(p.get("interval_start_s"),start);same(p.get("interval_end_s"),end)
            same(p.get("available_at_s"),end)
            if not isinstance(p.get("metrics"),dict):fail("missing_frame_metrics","rf_frames")
            pairs=set()
            for link in p.get("links",[]):
                a,b=link.get("source"),link.get("target")
                if a not in ids or b not in ids or a==b:fail("invalid_link_endpoint",str((a,b)))
                pair=tuple(sorted((a,b)))
                if pair in pairs or not isinstance(link.get("available"),bool):fail("invalid_link","duplicate/bool")
                pairs.add(pair);link_ids.add(link.get("id"))
            flat=[node for group in p.get("components",[]) for node in group]
            if set(flat)!=ids or len(flat)!=len(ids):fail("invalid_components","partition must cover fleet once")
        if abs(last_end-duration)>1e-8:fail("rf_duration_mismatch","last interval must end at simulation end")
        for time_s,observer,peer,payload in conn.execute("SELECT time_s,observer,peer,payload FROM observations ORDER BY time_s"):
            timestamp(time_s)
            if observer not in ids or peer not in ids or observer==peer:fail("invalid_observer_pair",observer+"/"+peer)
            p=decode_payload(payload)
            same(p.get("observed_at_s"),time_s);same(p.get("available_at_s"),time_s)
            if p.get("observer_id")!=observer or p.get("peer_id")!=peer:fail("observation_pair_mismatch","payload")
            values,valid=p.get("values"),p.get("valid")
            if not isinstance(values,dict) or not isinstance(valid,dict) or set(values)!=set(valid):fail("invalid_observation_mask","keys mismatch")
            for key,value in values.items():
                if key not in manifest.get("observation_definitions",{}):fail("unknown_observation_definition",key)
                if not isinstance(valid[key],bool):fail("invalid_observation_mask",key)
                if valid[key] and (not isinstance(value,(int,float,bool)) or not math.isfinite(value)):fail("invalid_observation_value",key)
                if not valid[key] and value is not None:fail("invalid_missing_observation",key)
            for age in p.get("ages",{}).values():
                if age is not None and (not isinstance(age,(int,float)) or age<0):fail("invalid_observation_age",str(age))
        for time_s,payload in conn.execute("SELECT time_s,payload FROM decisions ORDER BY time_s"):
            timestamp(time_s);p=decode_payload(payload);same(p.get("decision_time_s"),time_s)
            for action in p.get("actions",[]):
                if action.get("uav_id") not in ids:fail("invalid_action_uav","unknown")
                same(action.get("decision_time_s"),time_s)
                timestamp(action.get("interval_start_s"));timestamp(action.get("interval_end_s"))
                for field in ("requested_correction","applied_correction","actual_displacement"):
                    v=action.get(field)
                    if v is not None and (not isinstance(v,list) or len(v)!=2 or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in v)):
                        fail("invalid_action_vector",field)
            for prediction in p.get("predictions",[]):
                if prediction.get("observer_id") not in ids or prediction.get("peer_id") not in ids:fail("invalid_prediction_pair","unknown")
                same(prediction.get("generated_at_s"),time_s)
                if prediction.get("head_id") not in manifest.get("head_definitions",{}):fail("unknown_head",str(prediction.get("head_id")))
                value=prediction.get("value");support=prediction.get("supported")
                if not isinstance(support,bool):fail("invalid_prediction_support","bool required")
                if support and (not isinstance(value,(int,float)) or not 0<=value<=1):fail("invalid_probability","supported [0,1]")
                if not support and value is not None:fail("unsupported_prediction_value","must be null")
                if manifest.get("mode")=="physics" and (support or value is not None):fail("physics_prediction","must be not_applicable")
        for event_id,start,end,typ,payload in conn.execute("SELECT event_id,start_s,end_s,type,payload FROM events"):
            timestamp(start)
            if end is not None:timestamp(end)
            if end is not None and end<start:fail("invalid_event_interval",event_id)
            p=decode_payload(payload)
            if p.get("id")!=event_id or p.get("type")!=typ:fail("event_identity_mismatch",event_id)
            if any(x not in ids for x in p.get("related_uav_ids",[])):fail("invalid_event_uav",event_id)
            if p.get("temporal_kind")=="instant" and (end!=start or "right_censored" in p):fail("invalid_instant_event",event_id)
            if p.get("definition_id") not in manifest.get("event_definitions",{}):fail("unknown_event_definition",event_id)
        for metric_id,payload in conn.execute("SELECT metric_id,payload FROM metrics"):
            p=decode_payload(payload)
            if p.get("metric_id")!=metric_id or p.get("key") not in manifest["metric_definitions"]:fail("metric_definition_mismatch",metric_id)
            key=p["key"]
            if key not in manifest.get("final_metrics",{}) or "value" not in p:fail("missing_final_metric",key)
            expected=manifest["final_metrics"][key]
            if type(p["value"]) is not type(expected) and not (type(p["value"]) in (int,float) and type(expected) in (int,float)):
                fail("final_metric_type_mismatch",key)
            if p["value"]!=expected:fail("final_metric_value_mismatch",key)
    return dict(manifest,bundle_path=str(bundle))
