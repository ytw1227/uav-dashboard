"""Durable, sequential runs of selected dashboard regions at a pinned team revision."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from result_service.dashboard_contract.validation import confined, read_json, sha256
from .team_baseline_record import load_comparison
from .team_runtime import file_lock, map_coverage, prepare_version, research_runtime
from .team_source import WorkerInterrupted, check_worker_exit, source_path, source_status


PROJECT = Path(__file__).resolve().parents[1]
REGION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
SHA = re.compile(r"[0-9a-f]{40}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY, sha TEXT NOT NULL, source TEXT NOT NULL, core TEXT NOT NULL,
    python TEXT NOT NULL, added TEXT NOT NULL, quota_gib REAL NOT NULL DEFAULT 4.0
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, batch INTEGER NOT NULL REFERENCES batches(id),
    region TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL, message TEXT NOT NULL,
    finished_at TEXT, flights TEXT, UNIQUE(batch,region)
);
CREATE INDEX IF NOT EXISTS job_region ON jobs(region,id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TeamUpdates:
    def __init__(self, output_root, state_root=None, *, resume=True, max_output_gib=4.0):
        self.quota = float(max_output_gib)
        if not math.isfinite(self.quota) or self.quota <= 0:
            raise ValueError("결과 보관소 용량은 0보다 커야 합니다.")
        self.output_root = Path(output_root).resolve()
        self.state_root = Path(state_root or self.output_root.parent / "team-update").resolve()
        key = hashlib.sha256(os.path.normcase(str(self.output_root)).encode()).hexdigest()[:16]
        self.work = self.state_root / "stores" / key
        self.work.mkdir(parents=True, exist_ok=True)
        self.db = self.work / "updates.sqlite"
        self._coverage = None
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            if "quota_gib" not in {r["name"] for r in conn.execute("PRAGMA table_info(batches)")}:
                conn.execute("ALTER TABLE batches ADD COLUMN quota_gib REAL NOT NULL DEFAULT 4.0")
            pending = self._active(conn)
        if resume and pending:
            self._launch()

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.db, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    @staticmethod
    def _active(conn):
        return conn.execute("SELECT 1 FROM jobs WHERE state IN ('running','queued') LIMIT 1").fetchone() is not None

    def start(self, region, sha):
        return self.start_many([region], sha)

    def resume_demo_regions(self, batch_id, sha):
        """Extend an already requested all-region batch when its demo scenes become runnable."""
        def verify_latest(conn):
            latest = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
            if latest is None or latest["id"] != batch_id or latest["sha"] != sha:
                raise ValueError("요청한 전체 실행의 번호 또는 팀 코드 버전이 달라졌습니다.")
            return latest

        with self.connection() as conn:
            batch = verify_latest(conn)
        source = Path(batch["source"])
        source_status(source)
        approved = map_coverage(source, sha, Path(batch["core"]))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            verify_latest(conn)
            rows = conn.execute("SELECT id,region,state FROM jobs WHERE batch=?", (batch_id,)).fetchall()
            if {r["region"] for r in rows} != set(approved):
                raise ValueError("전체 지역 실행에서만 제외됐던 시연 지도를 이어갈 수 있습니다.")
            added = [r["id"] for r in rows if r["state"] == "unavailable"
                     and approved[r["region"]]["eligible"] and approved[r["region"]]["input_mode"] == "dashboard_demo"]
            conn.executemany("UPDATE jobs SET state='queued',phase='queued',message=?,finished_at=NULL "
                             "WHERE id=? AND state='unavailable'", [
                                 ("시연 지도를 확인해 같은 팀 버전으로 실행 대기 중", job) for job in added])
        if added:
            # The old worker queries this same queue after each flight. An extra
            # wake is harmless under its OS lock and covers a concurrent worker exit.
            self._launch()
        return len(added)

    def coverage(self, sha, *, refresh=False):
        path = source_path()
        if not SHA.fullmatch(str(sha)) or source_status(path)["commit"] != sha:
            raise ValueError("팀 코드 버전이 바뀌었습니다. 현재 버전을 확인하세요.")
        core, _ = research_runtime()
        stamp = (path, sha, core)
        if refresh or not self._coverage or self._coverage[0] != stamp:
            self._coverage = (stamp, map_coverage(path, sha, core))
        return self._coverage[1]

    def start_many(self, regions, sha):
        path = source_path()
        version = source_status(path)
        if not SHA.fullmatch(str(sha)) or version["commit"] != sha:
            raise ValueError("팀 코드 버전이 바뀌었습니다. 현재 표시된 버전을 다시 확인하세요.")
        core, python = research_runtime()
        approved = self.coverage(sha, refresh=True)
        regions = list(dict.fromkeys(regions))
        if not regions or any(not isinstance(r, str) or not REGION.fullmatch(r) or r not in approved for r in regions):
            raise ValueError("실행 가능한 지도 목록에서 지역을 선택하세요.")
        return self._enqueue(regions, sha, path, core, python, approved)

    def _enqueue(self, regions, sha, path, core, python, approved=None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._active(conn):
                raise ValueError("이미 지역 갱신이 진행 중입니다. 실행 현황을 확인하세요.")
            bid = conn.execute("INSERT INTO batches(sha,source,core,python,added,quota_gib) VALUES(?,?,?,?,?,?)",
                               (sha, str(path), str(core), str(python), now(), self.quota)).lastrowid
            for region in regions:
                if approved and not approved[region]["eligible"]:
                    conn.execute("INSERT INTO jobs(batch,region,state,phase,message,finished_at) VALUES(?,?,?,?,?,?)",
                                 (bid, region, "unavailable", "unavailable", approved[region]["reason"], now()))
                    continue
                current = next(self._published(conn, region), None)
                if current and current["sha"] == sha:
                    pin = current["flights"]["v21"]
                    bundle = self.output_root / "bundles" / pin["run_id"] / pin["revision"]
                    manifest = read_json(bundle / "manifest.json")
                    try:
                        needs_pair = manifest.get("adapter_id") == "team-halfsec" and load_comparison(bundle) is None
                    except (KeyError, ValueError, TypeError, OSError) as exc:
                        conn.execute("INSERT INTO jobs(batch,region,state,phase,message,finished_at) VALUES(?,?,?,?,?,?)",
                                     (bid, region, "failed", "failed", f"보관된 비교 기록 확인 필요 · {exc}", now()))
                        continue
                    if needs_pair:
                        conn.execute("INSERT INTO jobs(batch,region,state,phase,message,flights) VALUES(?,?,?,?,?,?)",
                                     (bid, region, "queued", "pair_pending", "등록된 비행의 기본 대형 비교를 준비합니다.",
                                      json.dumps(current["flights"])))
                    else:
                        conn.execute("INSERT INTO jobs(batch,region,state,phase,message,finished_at,flights) VALUES(?,?,?,?,?,?,?)",
                                     (bid, region, "completed", "reused", "이 버전으로 등록한 기록을 사용합니다.",
                                      current["finished_at"], json.dumps(current["flights"])))
                else:
                    conn.execute("INSERT INTO jobs(batch,region,state,phase,message) VALUES(?,?,?,?,?)",
                                 (bid, region, "queued", "queued", "차례를 기다리는 중"))
            launch = self._active(conn)
        if launch:
            try:
                self._launch()
            except OSError as exc:
                with self.connection() as conn:
                    conn.execute("UPDATE jobs SET state='failed',phase='failed',message=?,finished_at=? WHERE batch=? AND state='queued'",
                                 (f"실행 프로세스를 시작하지 못했습니다. {exc}", now(), bid))
                raise RuntimeError("실행 프로세스를 시작하지 못했습니다. 실행 현황을 확인하세요.") from exc
        return self.batch()

    def retry(self):
        with self.connection() as conn:
            batch = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
            if batch:
                regions = [r[0] for r in conn.execute("SELECT region FROM jobs WHERE batch=? AND state='failed' ORDER BY id", (batch["id"],))]
            if not batch or not regions:
                raise ValueError("다시 실행할 실패 지역이 없습니다.")
        return self._enqueue(regions, batch["sha"], batch["source"], batch["core"], batch["python"])

    def _launch(self):
        argv = [sys.executable, "-B", "-m", "nicegui_dashboard.team_update", "--output-root", str(self.output_root),
                "--state-root", str(self.state_root)]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (self.work / "worker.log").open("ab") as log:
            subprocess.Popen(argv, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             env=dict(os.environ, PYTHONUTF8="1"), **kwargs)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        data = dict(row)
        if data.get("flights"):
            data["flights"] = json.loads(data["flights"])
        return data

    def status(self, region):
        with self.connection() as conn:
            row = conn.execute("SELECT jobs.*,b.sha FROM jobs JOIN batches b ON b.id=jobs.batch WHERE region=? ORDER BY jobs.id DESC LIMIT 1",
                               (region,)).fetchone()
        return self._row(row) or {"state": "idle", "region": region, "sha": "", "phase": "", "message": "", "flights": None}

    def batch(self):
        with self.connection() as conn:
            batch = conn.execute("SELECT id,sha FROM batches ORDER BY id DESC LIMIT 1").fetchone()
            if not batch:
                return None
            items = [self._row(r) for r in conn.execute("SELECT region,state,phase,message,finished_at FROM jobs WHERE batch=? ORDER BY id", (batch["id"],))]
        counts = {k: sum(item["state"] == k for item in items) for k in ("queued","running","completed","failed","unavailable")}
        return {**dict(batch), **counts, "total": len(items), "items": items,
                "active": bool(counts["queued"] + counts["running"]),
                "region": next((item["region"] for item in items if item["state"] == "running"), None)}

    def _published(self, conn, region):
        rows = conn.execute("""SELECT sha,region,flights,finished_at FROM jobs JOIN batches b ON b.id=batch
                               WHERE region=? AND state='completed' AND phase='published' ORDER BY jobs.id DESC""", (region,))
        for raw in rows:
            row = self._row(raw)
            if self._available(row["flights"], region, row["sha"]):
                yield row

    def _available(self, flights, region, commit):
        """Recheck sealed identity and file sizes; full replay hashes were checked at registration."""
        if not isinstance(flights, dict) or "v21" not in flights:
            return False
        for pair in flights.values():
            if not isinstance(pair, dict) or not all(isinstance(pair.get(k), str) and REGION.fullmatch(pair[k])
                                                     for k in ("run_id", "revision")):
                return False
            try:
                root = confined(self.output_root, Path("bundles") / pair["run_id"] / pair["revision"], must_exist=False)
                ready = read_json(confined(root, "READY"), 4096)
                path = confined(root, "manifest.json")
                manifest = read_json(path)
                if not isinstance(ready, dict) or ready.get("manifest_sha256") != sha256(path):
                    return False
                identity = {"run_id": pair["run_id"], "bundle_revision": pair["revision"], "region": region, "team_commit": commit}
                if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in identity.items()):
                    return False
                artifacts = manifest.get("artifacts")
                if not isinstance(artifacts, dict) or not {"scene.json","data.sqlite"} <= artifacts.keys():
                    return False
                for name, reference in artifacts.items():
                    if not isinstance(reference, dict) or type(reference.get("size_bytes")) is not int:
                        return False
                    if confined(root, name).stat().st_size != reference["size_bytes"]:
                        return False
            except (OSError, ValueError):
                return False
        return True

    def current(self, region):
        with self.connection() as conn:
            return next(self._published(conn, region), None)

    def history(self, region):
        with self.connection() as conn:
            return list(self._published(conn, region))

    def _set(self, job_id, state, phase, message, flights=None):
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET state=?,phase=?,message=?,finished_at=?,flights=COALESCE(?,flights) WHERE id=?",
                         (state, phase, message, now() if state in ("failed", "completed") else None,
                          json.dumps(flights) if flights else None, job_id))

    def _run_recorder(self, job, arguments):
        request_dir = self.work / "attempts" / f"job-{job['id']}"
        request_dir.mkdir(parents=True, exist_ok=True)
        log_path = request_dir / "flight.log"
        with log_path.open("ab") as out:
            result = subprocess.run([job["python"], "-B", "-m", *arguments], cwd=PROJECT,
                                    stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", UAV_RESULT_MAX_GIB=str(job["quota_gib"]),
                                             OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1"))
        if result.returncode:
            check_worker_exit(result.returncode)
            with log_path.open("rb") as log:
                log.seek(max(0, log.seek(0, os.SEEK_END) - 3000))
                lines = log.read().decode("utf-8", errors="replace").strip().splitlines()
            detail = lines[-1][-400:] if lines else f"종료 코드 {result.returncode}"
            raise RuntimeError(f"{detail} (실행 기록 {log_path.name}, 작업 {request_dir.name})")

    def _capture(self, job, code):
        result_path = self.work / "attempts" / f"job-{job['id']}" / "result.json"
        self._run_recorder(job, ["nicegui_dashboard.team_runtime_child", "--core", job["core"],
                                 "--code", str(code), "--region", job["region"], "--commit", job["sha"],
                                 "--output-root", str(self.output_root), "--result", str(result_path)])
        raw = json.loads(result_path.read_text(encoding="utf-8"))
        if raw.get("team_commit") != job["sha"] or raw.get("region") != job["region"]:
            raise ValueError("결과의 지도 또는 팀 코드 버전이 선택한 실행과 일치하지 않습니다.")
        flights = {"v21":raw.get("v21")}
        if not self._available(flights, job["region"], job["sha"]):
            raise ValueError("완료 결과의 실행 식별자 또는 READY 표시를 확인하지 못했습니다.")
        return flights

    def _complete_comparison(self, job):
        flights = json.loads(job["flights"])
        if not self._available(flights, job["region"], job["sha"]):
            raise ValueError("비교할 기존 비행의 팀 버전 또는 지도 기록을 확인할 수 없습니다.")
        pin = flights["v21"]
        self._run_recorder(job, ["nicegui_dashboard.team_baseline", "--output-root", str(self.output_root),
                                 "--pin", pin["run_id"], pin["revision"]])
        if load_comparison(self.output_root / "bundles" / pin["run_id"] / pin["revision"]) is None:
            raise ValueError("비교 비행을 계산했지만 같은 조건의 결과를 찾을 수 없습니다.")

    def run_worker(self):
        with file_lock(self.work / "worker.lock", wait=True):
            with self.connection() as conn:
                # A recorder may survive the queue supervisor. Reattach to the
                # same attempt; its lock and result file prevent a duplicate run.
                conn.execute("UPDATE jobs SET state='queued',phase='queued',message=? WHERE state='running'",
                             ("이전 실행 기록을 확인해 이어서 진행합니다.",))
            while True:
                with self.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    job = conn.execute("""SELECT jobs.id,region,sha,source,core,python,quota_gib,flights FROM jobs JOIN batches b ON b.id=batch
                                          WHERE state='queued' ORDER BY jobs.id LIMIT 1""").fetchone()
                    if not job:
                        return
                    conn.execute("UPDATE jobs SET state='running',phase='preparing',message=? WHERE id=?",
                                 ("선택한 팀 버전과 연구 자료 준비 중", job["id"]))
                try:
                    if job["flights"]:
                        self._set(job["id"], "running", "pairing", "등록된 비행과 같은 조건의 기본 대형을 계산하는 중")
                        self._complete_comparison(job)
                        self._set(job["id"], "completed", "reused", "기존 팀 비행에 같은 버전의 비교 결과를 반영했습니다.")
                        continue
                    code = prepare_version(job["source"], job["sha"], Path(job["core"]), self.state_root)
                    self._set(job["id"], "running", "simulating", "팀 시뮬레이터 실행 및 결과 검증 중")
                    flights = self._capture(job, code)
                    self._set(job["id"], "completed", "published", "검증한 비행을 지역 카드에 반영했습니다.", flights)
                except WorkerInterrupted:
                    self._set(job["id"], "queued", "queued", "PC에서 실행이 중단됐습니다. 대시보드를 다시 열면 이어갑니다.")
                    return
                except Exception as exc:
                    self._set(job["id"], "failed", "failed", str(exc)[:700])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--resume-demo-batch", type=int, help="Extend exactly this existing full-region batch")
    parser.add_argument("--commit", help="Team commit of the existing batch")
    opts = parser.parse_args()
    if (opts.resume_demo_batch is None) != (opts.commit is None):
        parser.error("--resume-demo-batch and --commit must be provided together")
    updates = TeamUpdates(opts.output_root, opts.state_root, resume=False)
    if opts.resume_demo_batch is None:
        updates.run_worker()
    else:
        print(json.dumps({"resumed": updates.resume_demo_regions(opts.resume_demo_batch, opts.commit)}))
