"""Immutable portable shares. Standard library only; no research imports."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

BUNDLE_FILES = {"manifest.json", "scene.json", "data.sqlite", "READY"}
MAX_SHARE_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _portable(value):
    """Keep hashes and relative references, omit user-machine absolute locations."""
    if isinstance(value, dict):
        return {str(key): _portable(item) for key, item in value.items()
                if str(key) not in {"bundle_path", "project_root", "storage_dir", "research_python", "source_root"}}
    if isinstance(value, (list, tuple)):
        return [_portable(item) for item in value]
    if isinstance(value, str):
        if PureWindowsPath(value).is_absolute() or value.startswith(("file://", "/")):
            leaf = PureWindowsPath(value).name if "\\" in value or re.match(r"^[A-Za-z]:", value) else PurePosixPath(value).name
            return "[local path omitted]/" + leaf
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("Share payload must contain JSON-compatible values")


def create_snapshot(snapshot, destination):
    """Write a self-contained immutable HTML of an explicitly selected revision.

    snapshot requires run_id and bundle_revision. All supplied data is embedded;
    callers must query the requested window before passing it here. No fetch,
    local-file scan, inference or external asset is used by the resulting HTML.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be an object")
    snapshot = dict(snapshot)
    manifest = snapshot.get("manifest", {})
    selection = snapshot.get("selection", {})
    if not isinstance(manifest, dict) or not isinstance(selection, dict):
        raise ValueError("Invalid snapshot manifest or selection")
    selection = dict(selection)
    if "selected_time_s" not in selection and "time_s" in selection:
        selection["selected_time_s"] = selection["time_s"]
    if "window" not in selection and "from_s" in selection and "to_s" in selection:
        selection["window"] = {"start_s": selection["from_s"], "end_s": selection["to_s"]}
    snapshot["selection"] = selection
    snapshot.setdefault("run_id", manifest.get("run_id"))
    snapshot.setdefault("bundle_revision", manifest.get("bundle_revision"))
    if "window" not in snapshot and selection.get("window") is not None:
        snapshot["window"] = selection["window"]
    if not snapshot.get("run_id") or not snapshot.get("bundle_revision"):
        raise ValueError("Snapshot needs an explicit run_id and bundle_revision")
    payload = _portable(snapshot)
    payload.update(share_format="uav-dashboard-static-snapshot-v1",
                   generated_at=datetime.now(timezone.utc).isoformat(),
                   automatic_updates=False,
                   source_notice="Frozen export of the selected revision and supplied time window.")
    window = payload.get("window")
    if window is not None:
        if not isinstance(window, dict):
            raise ValueError("Snapshot window must be an object")
        start, end = window.get("start_s"), window.get("end_s")
        if start is not None or end is not None:
            if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or
                not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start):
                raise ValueError("Invalid selected time window")
    encoded = _json_bytes(payload)
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot is too large; select a shorter time window or export a bundle")
    data_sha = hashlib.sha256(encoded).hexdigest()
    safe_data = encoded.decode().replace("<", "\\u003c").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = html.escape(str(payload.get("title") or payload["run_id"]))
    revision = html.escape(str(payload["bundle_revision"]))
    template = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>__TITLE__ · 연구 결과 스냅샷</title><style>
body{font:15px/1.6 system-ui,sans-serif;margin:0;background:#f3f5f8;color:#18283a}
main{max-width:1120px;margin:32px auto;padding:26px;background:white;border:1px solid #d8e0ea;border-radius:14px}
h1{font-size:26px}.notice{padding:14px;background:#fff2c9;border-radius:8px}
small{overflow-wrap:anywhere}pre{overflow:auto;max-height:600px;padding:16px;background:#f5f7fb;border-radius:8px}
details{margin:16px 0;border-top:1px solid #d8e0ea;padding-top:12px}summary{cursor:pointer;font-weight:600}
canvas{display:block;width:100%;height:auto;background:#f5f7fb;border:1px solid #d8e0ea}
@media print{details{break-inside:avoid}pre{max-height:none;white-space:pre-wrap}main{margin:0}}
</style></head><body><main><h1>__TITLE__</h1>
<p class="notice">정적 스냅샷 · 자동 갱신 없음. 선택한 실행·revision·시간 범위의 데이터만 포함합니다. 로컬 결과 폴더를 검색하거나 연구를 실행하지 않습니다.</p>
<small>Revision: __REVISION__<br>내장 데이터 SHA-256: __HASH__</small>
<section id="map-section" hidden><h2>선택한 시간창의 저장 위치</h2><p id="map-time"></p><input id="map-slider" type="range" min="0" max="0" step="1" style="width:100%" aria-label="저장 위치 시각"><svg id="snapshot-map" viewBox="0 0 900 560" style="width:100%;height:auto;background:#edf2f7" aria-label="저장 데이터 지도"></svg><p>저장 위치 표본만 표시합니다. 화면은 사후 분석용이며 원본 결과가 추가되어도 갱신되지 않습니다.</p></section><div id="content"></div></main><script id="snapshot" type="application/json">__DATA__</script>
<script>
"use strict";
const payload=JSON.parse(document.getElementById("snapshot").textContent);
const host=document.getElementById("content");
const scene=payload.scene||{}, timeline=payload.timeline||{};
const positions=(timeline.positions||[]).filter(p=>p&&Array.isArray(p.uavs)&&Number.isFinite(p.sample_time_s));
if(!positions.length&&timeline.at&&timeline.at.position_before)positions.push(timeline.at.position_before);
const bounds=scene.bounds;
if(positions.length&&Array.isArray(bounds)&&bounds.length===4&&bounds.every(Number.isFinite)&&bounds[2]>bounds[0]&&bounds[3]>bounds[1]){
 const section=document.getElementById("map-section"), svg=document.getElementById("snapshot-map"), slider=document.getElementById("map-slider");
 section.hidden=false;slider.max=String(positions.length-1);
 const ns="http://www.w3.org/2000/svg", scale=Math.min(840/(bounds[2]-bounds[0]),500/(bounds[3]-bounds[1]));
 const dx=(900-scale*(bounds[2]-bounds[0]))/2,dy=(560-scale*(bounds[3]-bounds[1]))/2;
 const point=(x,y)=>[dx+(x-bounds[0])*scale,560-dy-(y-bounds[1])*scale];
 function node(tag,attrs,text){const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;svg.append(n);return n;}
 function draw(index){
  svg.replaceChildren();const p=positions[index], t=p.sample_time_s;
  for(const building of scene.buildings||[]){
   const g=building.geometry_local_m||building.geometry||{},polys=g.type==="Polygon"?[g.coordinates]:(g.type==="MultiPolygon"?g.coordinates:[]);
   for(const poly of polys||[]){const ring=poly&&poly[0];if(!Array.isArray(ring))continue;
    const points=ring.filter(q=>q&&Number.isFinite(q[0])&&Number.isFinite(q[1])).map(q=>point(q[0],q[1]).join(",")).join(" ");
    if(points)node("polygon",{points,fill:building.imputed?"#ccd1d8":"#a8b2bf",stroke:"#8795a6","stroke-width":1});
   }
  }
  const byId=new Map(p.uavs.map(u=>[String(u.id),u]));
  const eligible=(timeline.rf_frames||[]).filter(f=>Number.isFinite(f.available_at_s)&&f.available_at_s<=t);
  const frame=eligible.length?eligible[eligible.length-1]:(timeline.at&&timeline.at.link_frame&&timeline.at.link_frame.available_at_s<=t?timeline.at.link_frame:null);
  if(frame)for(const link of frame.links||[]){
   if(!link.available)continue;const a=byId.get(String(link.source)),b=byId.get(String(link.target));if(!a||!b)continue;
   const pa=point(a.x,a.y),pb=point(b.x,b.y);node("line",{x1:pa[0],y1:pa[1],x2:pb[0],y2:pb[1],stroke:"#4eaa97","stroke-width":2,opacity:.7});
  }
  for(const uav of p.uavs){
   if(!Number.isFinite(uav.x)||!Number.isFinite(uav.y))continue;const q=point(uav.x,uav.y);
   node("circle",{cx:q[0],cy:q[1],r:7,fill:uav.role==="search"?"#d77431":"#2261a3",stroke:"white","stroke-width":2});
   node("text",{x:q[0]+10,y:q[1]-8,fill:"#18334d","font-size":13},String(uav.id)+" · "+String(uav.role||"unknown"));
  }
  document.getElementById("map-time").textContent="위치 표본 "+t.toFixed(2)+" s · "+(frame?"링크 가용 시각 "+frame.available_at_s+" s":"이 시각까지 가용한 링크 표본 없음");
 }
 const requested=(payload.selection||{}).selected_time_s;
 if(Number.isFinite(requested)){let index=0;for(let k=0;k<positions.length;k++)if(positions[k].sample_time_s<=requested)index=k;slider.value=String(index);}
 slider.addEventListener("input",()=>draw(Number(slider.value)));draw(Number(slider.value));
}
for(const [key,value] of Object.entries(payload)){
 const section=document.createElement("details");section.open=["window","metrics","provenance","definitions","filters"].includes(key);
 const caption=document.createElement("summary");caption.textContent=key;
 const body=document.createElement("pre");body.textContent=JSON.stringify(value,null,2);
 section.append(caption,body);host.append(section);
}
</script></body></html>"""
    document = (template.replace("__TITLE__", title).replace("__REVISION__", revision)
                .replace("__HASH__", data_sha).replace("__DATA__", safe_data))
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently changing a previously shared snapshot.
    with target.open("xb") as stream:
        stream.write(document.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return {"filename": target.name, "format": payload["share_format"],
            "run_id": payload["run_id"], "bundle_revision": payload["bundle_revision"],
            "automatic_updates": False, "sha256": _sha(target),
            "size_bytes": target.stat().st_size, "content_sha256": data_sha}


def export_bundle(bundle_path, destination):
    """Export a validated local bundle as one portable, immutable ZIP."""
    from dashboard_contract.validation import validate_bundle
    source = Path(bundle_path).resolve(strict=True)
    manifest = validate_bundle(source, verify_hashes=True)
    for name in BUNDLE_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != source:
            raise ValueError("Bundle files must be regular files within the bundle")
    if sum((source / name).stat().st_size for name in BUNDLE_FILES) > MAX_SHARE_BYTES:
        raise ValueError("Bundle exceeds portable export limit")
    manifest = _portable(manifest)
    manifest_bytes = _json_bytes(manifest)
    ready = _json_bytes({"schema_version": manifest.get("schema_version", "1.0.0"),
                         "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()})
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    # x mode is intentional: an exported share is immutable.
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("READY", ready)
        archive.write(source / "scene.json", "scene.json")
        archive.write(source / "data.sqlite", "data.sqlite")
    validate_share_archive(target)
    return {"filename": target.name, "format": "uav-dashboard-bundle-zip-v1",
            "sha256": _sha(target), "size_bytes": target.stat().st_size,
            "run_id": manifest.get("run_id"), "bundle_revision": manifest.get("bundle_revision"),
            "automatic_updates": False}


def validate_share_archive(path, max_total_bytes=MAX_SHARE_BYTES):
    """Inspect an archive before extraction; no path or link is trusted."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_total_bytes:
        raise ValueError("Share archive is missing or too large")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(BUNDLE_FILES) or {entry.filename for entry in entries} != BUNDLE_FILES:
            raise ValueError("Share must contain exactly manifest.json, scene.json, data.sqlite, READY")
        total = 0
        for entry in entries:
            name = entry.filename
            if (name.startswith(("/", "\\")) or "\\" in name or ":" in name or
                    ".." in PurePosixPath(name).parts or entry.is_dir()):
                raise ValueError("Unsafe archive path")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Archive links are forbidden")
            if entry.flag_bits & 1:
                raise ValueError("Encrypted archives are unsupported")
            total += entry.file_size
            if entry.file_size > max_total_bytes or total > max_total_bytes:
                raise ValueError("Uncompressed share exceeds the configured limit")
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024 or archive.getinfo("READY").file_size > 65536:
            raise ValueError("Share metadata exceeds size limit")
        manifest = json.loads(archive.read("manifest.json"))
        ready = json.loads(archive.read("READY"))
        if hashlib.sha256(archive.read("manifest.json")).hexdigest() != ready.get("manifest_sha256"):
            raise ValueError("Share manifest hash does not match READY")
        if not isinstance(manifest, dict):
            raise ValueError("Invalid share manifest")
        for name in ("scene.json", "data.sqlite"):
            artifact = manifest.get("artifacts", {}).get(name)
            if not isinstance(artifact, dict):
                raise ValueError("Share artifact declaration is missing")
            if archive.getinfo(name).file_size != artifact.get("size_bytes"):
                raise ValueError("Share artifact size differs")
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            if digest.hexdigest() != artifact.get("sha256"):
                raise ValueError("Share artifact hash differs")
    return {"manifest": manifest, "uncompressed_bytes": total, "files": sorted(BUNDLE_FILES)}


def import_bundle(archive_path, destination_root):
    """Validate into a private staging folder, then publish a new immutable dir.

    This only imports a bundle. Catalog registration remains the local service's
    responsibility and must use the same bundle validator.
    """
    from dashboard_contract.validation import validate_bundle
    validate_share_archive(archive_path)
    root = Path(destination_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".share-import-", dir=root))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) != 4 or {item.filename for item in entries} != BUNDLE_FILES:
                raise ValueError("Archive changed or contains unsafe paths")
            if sum(item.file_size for item in entries) > MAX_SHARE_BYTES:
                raise ValueError("Archive expanded size exceeds limit")
            if any(stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1 for item in entries):
                raise ValueError("Archive links/encryption are forbidden")
            for entry in entries:
                target = stage / entry.filename
                with archive.open(entry) as source, target.open("xb") as output:
                    total = 0
                    while True:
                        data = source.read(1024 * 1024)
                        if not data:
                            break
                        total += len(data)
                        if total > MAX_SHARE_BYTES or total > entry.file_size:
                            raise ValueError("Archive entry exceeds declared size")
                        output.write(data)
        manifest = validate_bundle(stage, verify_hashes=True)
        # Registry scans bundles/run_id/revision/READY. Publish at that canonical
        # location, never an unregistered one-level import directory.
        destination = root / manifest["run_id"] / manifest["bundle_revision"]
        if not destination.resolve().is_relative_to(root):
            raise ValueError("Import destination escapes bundle root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        def same_revision():
            if destination.is_symlink() or not destination.resolve().is_relative_to(root):
                raise ValueError("Existing import destination escapes bundle root")
            existing = validate_bundle(destination, verify_hashes=True)
            left, right = dict(existing), dict(manifest)
            left.pop("bundle_path", None); right.pop("bundle_path", None)
            if left != right:
                raise ValueError("Immutable import revision conflicts with existing bundle")
        duplicate = destination.exists()
        if duplicate:
            same_revision()
        else:
            try:
                os.replace(stage, destination)
            except OSError:
                if not destination.exists():
                    raise
                same_revision()
                duplicate = True
        return {"bundle_path": str(destination), "run_id": manifest.get("run_id"),
                "bundle_revision": manifest.get("bundle_revision"), "imported": not duplicate,
                "duplicate": duplicate, "automatic_updates": False}
    finally:
        if stage.exists():
            if stage.is_symlink() or stage.resolve().parent != root:
                raise ValueError("Refusing cleanup outside the import staging root")
            shutil.rmtree(stage)


def create_bundle(run_document, artifacts, output_path, selected_window=None):
    """Compatibility API for the service: share the validated revision in full.

    The selected window is returned as viewing metadata; ZIP bytes include the
    complete run so receivers can replay it. Use create_snapshot for a cropped
    selection. artifacts is a mapping of the four expected names to local files.
    """
    if not isinstance(run_document, dict) or not isinstance(artifacts, dict):
        raise ValueError("Bundle export needs a manifest and artifact paths")
    if set(artifacts) != BUNDLE_FILES:
        raise ValueError("Bundle artifacts must include the four contract files")
    paths = {name: Path(path).resolve(strict=True) for name, path in artifacts.items()}
    parents = {path.parent for path in paths.values()}
    if len(parents) != 1 or any(paths[name].name != name for name in BUNDLE_FILES):
        raise ValueError("Artifacts must belong to one validated bundle directory")
    root = next(iter(parents))
    stored = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for key in ("run_id", "bundle_revision"):
        if run_document.get(key) != stored.get(key):
            raise ValueError("Requested run/revision does not match bundle")
    result = export_bundle(root, output_path)
    result.update(selected_window=_portable(selected_window), data_scope="complete_run",
                  selection_note="Bundle contains the full revision; the selected window is viewing metadata.")
    return result
