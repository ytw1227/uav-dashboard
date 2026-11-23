from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, math, os, sqlite3, uuid, zlib
from contextlib import contextmanager

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    with temporary.open("w",encoding="utf-8",newline="\n") as stream:
        json.dump(value,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def decode(payload):
    if isinstance(payload,bytes):
        decompressor=zlib.decompressobj()
        payload=decompressor.decompress(payload,1024*1024+1)
        if len(payload)>1024*1024 or not decompressor.eof:
            raise ValueError("oversize_compressed_row")
    return json.loads(payload)

@contextmanager
def database(bundle):
    path=(Path(bundle)/"data.sqlite").resolve()
    con=sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    try:
        yield con
    finally:
        con.close()

def finite_number(value, default=None):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value): return value
    return default

def completion_sort(row):
    stamp=row.get("completed_at")
    try:
        parsed=datetime.fromisoformat(stamp.replace("Z","+00:00")) if stamp else None
        if parsed is None or parsed.tzinfo is None:return (0,"",row.get("run_id",""))
        return (1,parsed.astimezone(timezone.utc).isoformat(),row.get("run_id",""))
    except (ValueError,AttributeError):return (0,"",row.get("run_id",""))

def metadata(manifest):
    keys=("region","seed","kind","direction","mode","split","controller","map_id","map_hash",
          "model_id","research_version","config_hash","mission_completed","simulation_end_s",
          "interference","placement","termination_rule","evaluation_window","execution_phase","run_purpose","source_map_split","source_schema","adapter_id","research_family")
    result={key:manifest.get(key) for key in keys}
    result.update(manifest.get("metadata",{}))
    if not result.get("research_family"):
        result["research_family"]="final_v1" if manifest.get("adapter_id")=="final-v1" or manifest.get("research_version")=="Final V1" else "legacy_v5"
    if result.get("mission_completed") is None:result["mission_completed"]=manifest.get("final_metrics",{}).get("mission_completed")
    result["mission_status"]="completed" if result.get("mission_completed") is True else "not_completed" if result.get("mission_completed") is False else "not_assessed"
    return result

def summary(manifest):
    keys=("run_id","bundle_revision","artifact_kind","stage_status","export_status","started_at",
          "completed_at","completion_time_status","capabilities","source_key","conflict","simulation_end_s","exported_at")
    result={key:manifest.get(key) for key in keys}
    result["metadata"]=metadata(manifest)
    result["label"]=manifest.get("label") or " / ".join(str(result["metadata"].get(k) or "미기록") for k in ("region","mode","seed"))
    result["metrics"]=manifest.get("final_metrics",{})
    return result
