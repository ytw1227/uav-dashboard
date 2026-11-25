"""Reconstructible catalog, immutable revision pins and serialized bounded exports."""
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
import hashlib, json, math, os, sqlite3, subprocess, threading, time
from dashboard_contract.validation import validate_bundle, read_json, ContractError, confined, sha256
from dashboard_contract.definitions import metric_definition
from .common import utcnow, digest, summary, completion_sort
from .storage_policy import allocated_bytes, compression_enabled

class Registry:
    def __init__(self,project_root,output_root,export_python=None,interval_s=10,max_exports=4,scan_raw=True,max_output_gib=4.0):
        self.project=Path(project_root).resolve();self.output=Path(output_root).resolve()
        if self.project.is_relative_to(self.output) or any(self.output.is_relative_to(self.project/name) for name in ("results","maps","model","models","solo","cooperative","configs","settings","docs","code")):
            raise ValueError("dashboard output must be separate from research files")
        quota=float(max_output_gib)
        if not math.isfinite(quota) or quota<=0:raise ValueError("max_output_gib must be finite and positive")
        self.max_output_bytes=int(quota*1024**3)
        self.output.mkdir(parents=True,exist_ok=True)
        self.db=self.output/"catalog.sqlite";self.export_python=export_python
        self.interval=max(1,float(interval_s));self.max_exports=max(1,int(max_exports));self.scan_raw=scan_raw
        self.lock=threading.RLock();self.scan_lock=threading.Lock();self.wake=threading.Event();self.stop_event=threading.Event()
        self.generation=0;self.scanning=False;self.last_scan_at=None;self.next_scan_at=None;self.current=None
        self.progress={};self.bundle_cache={};self.manifest_cache={};self.thread=None
        self.scan_deferred_reason=None;self.output_bytes=0
        with self.connection() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS records(
              run_id TEXT,revision TEXT,manifest TEXT NOT NULL,bundle TEXT,source_path TEXT,
              PRIMARY KEY(run_id,revision));
              CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY,signature TEXT,status TEXT,
              retry_after REAL DEFAULT 0,detail TEXT);
              CREATE TABLE IF NOT EXISTS errors(key TEXT PRIMARY KEY,detail TEXT,updated_at TEXT);
              CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS revision_pins(run_id TEXT,revision TEXT,manifest_hash TEXT NOT NULL,
              PRIMARY KEY(run_id,revision));""")
            c.execute("INSERT OR IGNORE INTO state VALUES('generation','0')")
            self.generation=int(c.execute("SELECT value FROM state WHERE key='generation'").fetchone()[0])
            # Upgrade an existing catalog without losing its already accepted immutable identity.
            for rid,rev,raw in c.execute("SELECT run_id,revision,manifest FROM records WHERE bundle<>''").fetchall():
                c.execute("INSERT OR IGNORE INTO revision_pins VALUES(?,?,?)",(rid,rev,digest(json.loads(raw))))

    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.db,timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:yield conn
        finally:conn.close()

    @contextmanager
    def _output_scan_lock(self):
        # Stable OS-held lock: no PID reuse, stale-file deletion, or cross-process race.
        # A crash releases the lock; the harmless lock file remains for the next process.
        path=self.output/".registry.scan.lock"
        if path.is_symlink():raise ValueError("scan_lock_must_not_be_symlink")
        stream=path.open("a+b");locked=False
        try:
            if stream.seek(0,2)==0:stream.write(b"0");stream.flush()
            stream.seek(0)
            try:
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                locked=True
            except (OSError,BlockingIOError):pass
            yield locked
        finally:
            if locked:
                stream.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close()

    def _bump(self,c):
        c.execute("UPDATE state SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
        self.generation=int(c.execute("SELECT value FROM state WHERE key='generation'").fetchone()[0])

    def _safe_message(self,message):
        value=str(message)
        for path,label in ((self.project,"<project>"),(self.output,"<dashboard-output>")):
            value=value.replace(str(path),label).replace(path.as_posix(),label)
        return value[:1500]

    def error(self,key,code,message):
        value={"source":key,"code":code,"message":self._safe_message(message),"retryable":True,"updated_at":utcnow()}
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO errors VALUES(?,?,?)",(key,json.dumps(value),value["updated_at"]))

    def clear_error(self,key):
        with self.connection() as c:c.execute("DELETE FROM errors WHERE key=?",(key,))

    @staticmethod
    def _episode_source(manifest):
        for ref in manifest.get("source_refs",[]):
            if not isinstance(ref,dict) or ref.get("kind")!="episode_metadata":continue
            value=ref.get("path")
            if not isinstance(value,str):continue
            path=PurePosixPath(value)
            if not path.is_absolute() and ".." not in path.parts and "\\" not in value and ":" not in value and value.startswith(("results/transformer/", "code/outputs/dashboard_demo/")) and value.endswith(".json"):
                return value
        return None

    def _matched_local_source(self,manifest):
        # Imported bundles can reuse relative paths from another project. Bind
        # local promotion/cache state only when the exact metadata bytes match.
        relative=self._episode_source(manifest)
        if not relative:return None
        path=(self.project/relative).resolve()
        if not any(path.is_relative_to((self.project/base).resolve()) for base in ("results/transformer", "code/outputs/dashboard_demo")):return None
        expected=next((ref.get("sha256") for ref in manifest.get("source_refs",[])
                       if isinstance(ref,dict) and ref.get("kind")=="episode_metadata" and ref.get("path")==relative),None)
        if not isinstance(expected,str) or len(expected)!=64:return None
        try:
            if path.stat().st_size>32*1024*1024:return None
            with path.open("rb") as stream:actual=hashlib.file_digest(stream,"sha256").hexdigest()
        except OSError:return None
        return relative if actual==expected else None

    def put(self,manifest,bundle=None,source_path=None):
        doc=dict(manifest);doc.pop("bundle_path",None)
        raw=json.dumps(doc,sort_keys=True,ensure_ascii=False,allow_nan=False)
        rid,rev=doc["run_id"],doc["bundle_revision"]
        if bundle:source_path=self._matched_local_source(doc)
        with self.lock,self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if bundle:
                pin=c.execute("SELECT manifest_hash FROM revision_pins WHERE run_id=? AND revision=?",(rid,rev)).fetchone()
                fingerprint=digest(doc)
                if pin and pin[0]!=fingerprint:raise ContractError("immutable_revision_changed","Previously accepted run/revision was changed")
                c.execute("INSERT OR IGNORE INTO revision_pins VALUES(?,?,?)",(rid,rev,fingerprint))
            previous=c.execute("SELECT manifest,bundle,source_path FROM records WHERE run_id=? AND revision=?",(rid,rev)).fetchone()
            removed=0
            if source_path and bundle:
                removed=c.execute("DELETE FROM records WHERE source_path=? AND bundle=''",(source_path,)).rowcount
            if previous and previous==(raw,str(bundle or ""),source_path):
                if removed:self._bump(c)
                return bool(removed)
            c.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)",(rid,rev,raw,str(bundle or ""),source_path))
            self._bump(c)
        return True

    def _quarantine(self,path,exc):
        path=Path(path);key="bundle:"+path.relative_to(self.output).as_posix()
        self.error(key,getattr(exc,"code","bundle_validation_failed"),exc)
        with self.lock,self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("DELETE FROM records WHERE bundle=?",(str(path),)).rowcount:self._bump(c)
        self.bundle_cache.pop(str(path),None);self.manifest_cache.pop(str(path),None)

    def _validated(self,path):
        if path.is_symlink() or not path.resolve().is_relative_to(self.output/"bundles"):
            raise ValueError("bundle_outside_root")
        signature=self._bundle_signature(path)
        if self.bundle_cache.get(str(path))==signature and str(path) in self.manifest_cache:
            return self.manifest_cache[str(path)],signature
        with self.connection() as conn:
            accepted=conn.execute("SELECT r.manifest,p.manifest_hash FROM records r JOIN revision_pins p "
                                  "ON r.run_id=p.run_id AND r.revision=p.revision WHERE r.bundle=?",
                                  (str(path),)).fetchone()
        if accepted:
            previous=json.loads(accepted[0])
            manifest_path=confined(path,"manifest.json")
            document=read_json(manifest_path)
            if (digest(previous)==accepted[1] and digest(document)==accepted[1]
                    and read_json(confined(path,"READY"),4096).get("manifest_sha256")==sha256(manifest_path)):
                # These exact bytes already passed full row validation when
                # registered. Re-hash every artifact after restart, avoiding
                # decoding millions of identical rows while holding a lock.
                unchanged=True
                for name,reference in document["artifacts"].items():
                    artifact=confined(path,name)
                    if artifact.stat().st_size!=reference["size_bytes"] or sha256(artifact)!=reference["sha256"]:
                        unchanged=False
                        break
                if unchanged:
                    return document,signature
        doc=validate_bundle(path);doc.pop("bundle_path",None)
        return doc,signature

    def get(self,run_id,revision):
        if not revision:raise ValueError("revision_required: choose an immutable export revision")
        with self.connection() as c:
            row=c.execute("SELECT manifest,bundle FROM records WHERE run_id=? AND revision=?",(run_id,revision)).fetchone()
        if not row:raise KeyError("run_revision_not_found")
        doc=json.loads(row[0])
        if row[1]:
            path=Path(row[1])
            try:
                current,signature=self._validated(path)
                if digest(current)!=digest(doc):raise ContractError("immutable_revision_changed","Previously accepted run/revision was changed")
                self.bundle_cache[str(path)]=signature;self.manifest_cache[str(path)]=current
            except Exception as exc:
                self._quarantine(path,exc);raise
            return doc,path
        return doc,None

    def list(self):
        with self.connection() as c:
            self.generation=int(c.execute("SELECT value FROM state WHERE key='generation'").fetchone()[0])
            rows=[json.loads(r[0]) for r in c.execute("SELECT manifest FROM records")]
            errors=c.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        identities={}
        for m in rows:identities.setdefault(m.get("source_key"),set()).add(m["run_id"])
        result=[]
        for m in rows:
            item=summary(m);item["conflict"]=len(identities.get(m.get("source_key"),set()))>1;result.append(item)
        result.sort(key=lambda r:(completion_sort(r),r.get("bundle_revision","")),reverse=True)
        return {"runs":result,"generation":self.generation,"last_scan_at":self.last_scan_at,"error_count":errors}

    def status(self):
        with self.connection() as c:
            self.generation=int(c.execute("SELECT value FROM state WHERE key='generation'").fetchone()[0])
            errors=[json.loads(r[0]) for r in c.execute("SELECT detail FROM errors ORDER BY updated_at DESC")]
            counts=dict(c.execute("SELECT status,COUNT(*) FROM sources GROUP BY status").fetchall())
        return {"generation":self.generation,"scanning":self.scanning,"last_scan_at":self.last_scan_at,
                "next_scan_at":self.next_scan_at,"interval_s":self.interval,"current":self.current,
                "errors":errors,"counts":counts,"progress":dict(self.progress),
                "raw_export_enabled":bool(self.export_python and self.scan_raw),"update_mode":"completed_results_only",
                "scan_deferred_reason":self.scan_deferred_reason,
                "storage":{"observed_bytes":self.output_bytes,"max_bytes":self.max_output_bytes,
                           "quota_basis":"allocated" if compression_enabled(self.output) else "logical",
                           "scope":"entire dashboard output; quota checked before and during automatic export",
                           "partial_staging":"retained on export failure; never registered without validated READY"}}

    @staticmethod
    def _bundle_signature(path):
        return tuple((name,(path/name).stat().st_size,(path/name).stat().st_mtime_ns) for name in ("READY","manifest.json","scene.json","data.sqlite"))

    def discover_bundles(self, *, share_output=False):
        with self.connection() as c:known=[Path(r[0]) for r in c.execute("SELECT DISTINCT bundle FROM records WHERE bundle<>''")]
        candidates={ready.parent for ready in (self.output/"bundles").glob("*/*/READY")}
        # Missing READY is also a validation failure, not an invisible stale completed row.
        for path in sorted(candidates|set(known)):
            if self.stop_event.is_set():
                break
            if share_output:
                # Completed bundles become visible by atomic rename. Release
                # the writer lock between bundles so a cold catalog scan
                # cannot block publication for the entire retained history.
                with self._output_scan_lock() as acquired:
                    if not acquired:
                        self.scan_deferred_reason="another_registry_owns_output_scan"
                        break
                    self._discover_bundle(path)
                time.sleep(.01)
            else:
                self._discover_bundle(path)

    def _discover_bundle(self, path):
        try:
            manifest,signature=self._validated(path)
            self.put(manifest,path)
            self.bundle_cache[str(path)]=signature;self.manifest_cache[str(path)]=manifest
            self.clear_error("bundle:"+path.relative_to(self.output).as_posix())
        except Exception as exc:self._quarantine(path,exc)

    def _source_signature(self,path):
        # Cheap change detection only; the exporter verifies cryptographic source hashes.
        values=[]
        for f in (path,path.with_suffix(".npz"),path.parent/"summary.json",
                  self.project/"configs/transformer_plan.json",self.project/"results/transformer/bootstrap/complete.json",
                  self.project/"FILE_MANIFEST.json", self.project/"code/final_v1/artifacts/release.json"):
            try:st=f.stat();values.append((f.name,st.st_size,st.st_mtime_ns))
            except FileNotFoundError:values.append((f.name,None))
        return digest(values)

    def _source_state(self,path,signature,status,detail=None,retry=0):
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",(path,signature,status,retry,self._safe_message(detail) if detail else None))

    def _has_source(self,rel,bundle_only=False):
        sql="SELECT bundle FROM records WHERE source_path=?"+(" AND bundle<>''" if bundle_only else "")
        with self.connection() as c:rows=c.execute(sql,(rel,)).fetchall()
        return any(not value or (Path(value)/"READY").is_file() for (value,) in rows)

    def _summary_record(self,rel,raw,kind,status,capabilities,reason=None):
        stable={k:raw.get(k) for k in ("execution_signature","checkpoint_sha256","region","seed","direction","kind","mode")}
        source_key="source-"+digest(stable if kind=="episode_summary" else {"path":rel})[:32]
        content=digest(raw)
        rid=("summary-" if kind=="episode_summary" else "parent-")+digest([source_key,content])[:32]
        rev="summary-"+content[:24]
        numeric={k:v for k,v in raw.items() if isinstance(v,(int,float,bool)) and k not in {"seed"}}
        definitions={k:metric_definition(k) for k in numeric}
        parts=Path(rel).parts;split=next((s for s in ("validation","train","test") if s in parts),None)
        record={"schema_version":"1.0.0","run_id":rid,"bundle_revision":rev,"source_key":source_key,
                "source_identity":{"path":rel,"json_sha256":content},"artifact_kind":kind,
                "stage_status":status,"export_status":"summary_only","started_at":None,"completed_at":None,
                "completion_time_status":"unknown","capabilities":capabilities,
                "research_version":raw.get("source_version") or raw.get("format"),
                "model_id":raw.get("checkpoint_sha256"),"config_hash":raw.get("plan_sha256"),
                "source_refs":[{"path":rel,"hash_kind":"canonical_json","sha256":content}],
                "metadata":{k:raw.get(k) for k in ("region","seed","kind","direction","mode","controller","mission_completed")},
                "split":split,"simulation_end_s":raw.get("duration_s"),"final_metrics":numeric,
                "metric_definitions":definitions,"missing_reason":reason,
                "label":("요약 · " if kind=="episode_summary" else "상위 집계 · ")+Path(rel).parent.name+"/"+Path(rel).stem}
        if kind!="episode_summary":
            record["stored_aggregates"]={k:raw[k] for k in ("communication","prediction","alarms","cases","updates","committed_updates","training_episodes","last","train_episodes","validation_episodes") if k in raw}
            record["summary_metadata"]={k:raw[k] for k in ("mode","split","checkpoint","updates","notes") if k in raw}
            if raw.get("source_sha256"):record["summary_metadata"]["research_fingerprint"]=digest(raw["source_sha256"])
            record["child_source_refs"]=raw.get("raw_paths",raw.get("train_paths",[])+raw.get("validation_paths",[]))
            record["aggregation_scope"]="parent; never counted as independent episode"
        return record

    def _output_usage(self):
        total=0
        physical=compression_enabled(self.output)
        for folder,dirs,files in os.walk(self.output,followlinks=False):
            dirs[:]=[name for name in dirs if not (Path(folder)/name).is_symlink() and (Path(folder)/name).resolve().is_relative_to(self.output)]
            for name in files:
                path=Path(folder)/name
                try:
                    if not path.is_symlink() and path.resolve().is_relative_to(self.output):
                        total+=allocated_bytes(path) if physical else path.stat().st_size
                except FileNotFoundError:pass
        self.output_bytes=total
        return total

    def _run_export(self,command):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",PYTHONIOENCODING="utf-8")
        started=time.monotonic()
        # The optional exporter belongs to the external research runtime.
        process=subprocess.Popen(command,cwd=self.project,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace",
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            while True:
                try:stdout,stderr=process.communicate(timeout=.5);break
                except subprocess.TimeoutExpired:pass
                if self.stop_event.is_set():raise ContractError("export_interrupted","Service stopped; partial staging retained")
                if time.monotonic()-started>600:raise ContractError("export_timeout","Exporter exceeded 600 s; partial staging retained")
                if self._output_usage()>=self.max_output_bytes:
                    raise ContractError("storage_quota_exceeded","Automatic export interrupted at output quota; partial staging retained and not registered")
            if process.returncode:
                raise ContractError("exporter_failed",(stderr or stdout)[-1200:]+"; incomplete staging may remain; no partial result is registered")
            if self._output_usage()>self.max_output_bytes:
                raise ContractError("storage_quota_exceeded","Export reached the output quota; completed or partial output is retained, further automatic exports are pending")
            result=json.loads(stdout)
            if not isinstance(result,dict) or not result.get("ok") or not result.get("run_id") or not result.get("bundle_revision"):
                raise ContractError("invalid_exporter_response","Exporter did not identify a completed immutable revision")
            return result
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def discover_sources(self,force=False):
        if not self.scan_raw:return
        roots=[self.project/"results/transformer",self.project/"code/outputs/dashboard_demo"]
        roots=[root.resolve() for root in roots if root.is_dir()]
        if not roots:return
        paths=sorted((f for root in roots for f in root.rglob("*.json")),
                     key=lambda f:(0 if "evaluation" in f.parts else 1,str(f)))
        exports=0;found=0;waiting=0
        for path in paths:
            if self.stop_event.is_set():break
            rel=path.relative_to(self.project).as_posix();signature=None
            try:
                if not any(path.resolve().is_relative_to(root) for root in roots):raise ValueError("source_outside_results")
                if path.stat().st_size>32*1024*1024:raise ContractError("oversize_json","JSON exceeds 32 MiB")
                signature=self._source_signature(path)
                with self.connection() as c:
                    previous=c.execute("SELECT signature,status,retry_after FROM sources WHERE path=?",(rel,)).fetchone()
                if previous and previous[0]==signature and not force:
                    if previous[1]=="ignored":continue
                    if previous[1] in ("ready","summary_only","parent") and self._has_source(rel,previous[1]=="ready"):continue
                    if previous[2]>time.time():continue
                raw=read_json(path)
                if not isinstance(raw,dict):self._source_state(rel,signature,"ignored");continue
                if raw.get("schema"):
                    found+=1
                    final_v1=raw["schema"]=="final-v1-dashboard-raw-v1"
                    if raw["schema"] not in ("standalone-transformer-rf-v1", "final-v1-dashboard-raw-v1"):
                        raise ContractError("unsupported_source_schema",str(raw["schema"]))
                    if final_v1 and not rel.startswith("code/outputs/dashboard_demo/"):
                        raise ContractError("unsupported_source_location",rel)
                    marker=raw.get("dashboard_provenance",{}).get("array_sha256") if final_v1 else raw.get("array_sha256")
                    if not marker:
                        waiting+=1;self.error(rel,"waiting_completion_marker","array_sha256 commit marker is absent")
                        self._source_state(rel,signature,"waiting",retry=time.time()+self.interval);continue
                    npz=path.with_suffix(".npz")
                    if not npz.is_file():
                        if final_v1:
                            waiting+=1;self.error(rel,"waiting_arrays","Final V1 array file is absent")
                            self._source_state(rel,signature,"waiting",retry=time.time()+self.interval);continue
                        if self._has_source(rel,True):
                            self._source_state(rel,signature,"ready");self.clear_error(rel);continue
                        record=self._summary_record(rel,raw,"episode_summary","completion_unverified",
                                                    {"summary":True,"trajectory":False,"rf_observations":False},
                                                    "raw_unavailable; hash pair cannot be verified")
                        self.put(record,source_path=rel);self._source_state(rel,signature,"summary_only");self.clear_error(rel);continue
                    if not self.export_python:
                        waiting+=1;self._source_state(rel,signature,"waiting_exporter",retry=time.time()+self.interval);continue
                    if exports>=self.max_exports:waiting+=1;continue
                    if self._output_usage()>=self.max_output_bytes:
                        waiting+=1;self.error(rel,"storage_quota_exceeded","Automatic export pending: dashboard output quota reached; existing files preserved")
                        self._source_state(rel,signature,"waiting_quota",retry=time.time()+self.interval);continue
                    self.current=rel;exports+=1
                    command=[self.export_python,"-B","-m","dashboard_export.final_v1" if final_v1 else "dashboard_export","--project-root",str(self.project),
                             "--prefix",path.with_suffix("").relative_to(self.project).as_posix(),"--output-root",str(self.output)]
                    result=self._run_export(command);self.discover_bundles()
                    try:
                        published,bundle=self.get(result["run_id"],result["bundle_revision"])
                    except (KeyError,TypeError) as exc:
                        raise ContractError("export_not_registered","Exporter did not publish its declared immutable revision; incomplete staging may remain") from exc
                    if not bundle or self._matched_local_source(published)!=rel:
                        raise ContractError("export_not_registered","Exporter did not publish a validated bundle for this source; incomplete staging may remain")
                    self._source_state(rel,signature,"ready");self.clear_error(rel)
                elif raw.get("format")=="standalone-transformer-evaluation-v5" and path.name=="summary.json":
                    self.put(self._summary_record(rel,raw,"evaluation_parent","completed",{"summary":True}),source_path=rel)
                    self._source_state(rel,signature,"parent");self.clear_error(rel)
                elif path.name=="complete.json" and "train_paths" in raw and "validation_paths" in raw:
                    self.put(self._summary_record(rel,raw,"bootstrap_parent","completed",{"summary":True}),source_path=rel)
                    self._source_state(rel,signature,"parent");self.clear_error(rel)
                elif path.name in ("progress.json","history.json") and "train" in path.parts:
                    self.put(self._summary_record(rel,raw,"training_progress","completion_unknown",{"summary":True}),source_path=rel)
                    self._source_state(rel,signature,"parent");self.clear_error(rel)
                else:self._source_state(rel,signature,"ignored")
            except Exception as exc:
                code=getattr(exc,"code","source_validation_or_export_failed")
                self.error(rel,code,exc)
                self._source_state(rel,signature,"waiting_quota" if code=="storage_quota_exceeded" else "error",
                                   str(exc),time.time()+max(30,self.interval))
            finally:self.progress={"candidates_found":found,"exports_this_scan":exports,"deferred":waiting}
        self.current=None

    def scan_once(self,force=False):
        if not self.scan_lock.acquire(blocking=False):return False
        try:
            with self._output_scan_lock() as acquired:
                if not acquired:
                    self.scan_deferred_reason="another_registry_owns_output_scan";return False
                self.scan_deferred_reason=None;self.scanning=True
                try:
                    if self.scan_raw:
                        self.discover_bundles();self.discover_sources(force=force);self._output_usage()
                except Exception as exc:self.error("registry","scan_failed",exc)
            if not self.scan_raw:
                try:
                    self.discover_bundles(share_output=True);self._output_usage()
                except Exception as exc:self.error("registry","scan_failed",exc)
            self.scanning=False;self.current=None;self.last_scan_at=utcnow()
            from datetime import datetime,timezone,timedelta
            self.next_scan_at=(datetime.now(timezone.utc)+timedelta(seconds=self.interval)).isoformat()
            return True
        finally:self.scan_lock.release()

    def start(self):
        if self.thread and self.thread.is_alive():return
        def loop():
            while not self.stop_event.is_set():
                self.scan_once();self.wake.wait(self.interval);self.wake.clear()
        self.thread=threading.Thread(target=loop,name="dashboard-registry",daemon=True);self.thread.start()

    def rescan(self):
        with self.connection() as c:c.execute("UPDATE sources SET retry_after=0 WHERE status IN ('error','waiting','waiting_exporter','waiting_quota')")
        self.wake.set();return self.status()

    def close(self):
        self.stop_event.set();self.wake.set()
        if self.thread:self.thread.join(timeout=3)
