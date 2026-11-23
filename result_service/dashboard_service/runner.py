"""Opt-in, bounded research CLI jobs. No research modules are imported."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
import uuid

ALLOWED_COMMANDS = ("evaluate", "doctor", "verify")
_ACTIVE = {"starting", "running", "stopping", "ownership_unknown"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _atomic(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class _WindowsJob:
    """Keep an OS handle to this launch's process tree, never kill by reused PID."""
    def __init__(self, process):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_uint), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint), ("SchedulingClass", ctypes.c_uint)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Cannot create owned process job")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise OSError(ctypes.get_last_error(), "Cannot secure owned process-tree cleanup")
        if not kernel.AssignProcessToJobObject(self.handle, ctypes.c_void_p(int(process._handle))):
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise OSError(ctypes.get_last_error(), "Cannot establish process-tree ownership")

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 130):
            raise OSError(ctypes.get_last_error(), "Cannot stop owned process tree")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class JobManager:
    """A disabled-by-default launcher; stored timestamps describe parent jobs only.

    fixture_script is an explicit constructor-only QA hook. It never comes from
    an HTTP request and all resulting jobs are visibly marked synthetic_fixture.
    """
    def __init__(self, storage_dir, project_root, research_python=None, enabled=False,
                 *, fixture_script=None, on_complete=None):
        self.storage_dir = Path(storage_dir).resolve()
        self.project_root = Path(project_root).resolve(strict=True)
        if self.project_root.is_relative_to(self.storage_dir) or any(
                self.storage_dir.is_relative_to(self.project_root / name)
                for name in ("results", "maps", "model", "models", "solo", "cooperative", "configs", "settings", "docs")):
            raise ValueError("Launcher storage must be separate from research files")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.research_python = Path(research_python).resolve(strict=True) if research_python else None
        self.enabled = bool(enabled)
        self.fixture_script = Path(fixture_script).resolve(strict=True) if fixture_script else None
        self.on_complete = on_complete
        self.owner_nonce = uuid.uuid4().hex
        self._mutex = threading.RLock()
        self._owned = {}

    def capabilities(self):
        return {"enabled": self.enabled and self.research_python is not None,
                "configured": self.research_python is not None,
                "commands": list(ALLOWED_COMMANDS), "training_enabled": False,
                "live_telemetry": False,
                "fixture": self.fixture_script is not None,
                "limitations": ["Evaluation is limited to one case and one worker.",
                                "Job times are parent-process observations, not episode timestamps.",
                                "Jobs from a prior service instance cannot be stopped by PID."]}

    def _record_path(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
            raise ValueError("Invalid job id")
        return self.storage_dir / ("job-" + job_id + ".json")

    def get_job(self, job_id):
        path = self._record_path(job_id)
        if not path.is_file():
            raise KeyError("Job not found")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") in {"starting", "running", "stopping"} and job_id not in self._owned:
            record = dict(record, status="ownership_unknown",
                          status_note="This service instance cannot establish live ownership; PID-based stop is refused.")
        return record

    def list_jobs(self):
        records = []
        for path in self.storage_dir.glob("job-*.json"):
            try:
                records.append(self.get_job(path.stem.removeprefix("job-")))
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda row: row.get("started_at", ""), reverse=True)

    def _command(self, spec):
        if not isinstance(spec, dict):
            raise ValueError("Job specification must be an object")
        if set(spec) - {"command", "run", "checkpoint", "cases", "workers"}:
            raise ValueError("Unsupported job parameter")
        command = spec.get("command")
        if command not in ALLOWED_COMMANDS:
            raise ValueError("Only evaluate, doctor, and verify are allowed; training is disabled")
        if self.research_python is None or not self.research_python.is_file():
            raise ValueError("Configure an explicit research_python executable before starting jobs")
        script = self.fixture_script or self.project_root / "transformer_run.py"
        script = script.resolve(strict=True)
        if self.fixture_script is None and not script.is_relative_to(self.project_root):
            raise ValueError("Research launcher escapes the configured project")
        args = [str(self.research_python), "-B", str(script), command]
        clean = {"command": command}
        if command == "evaluate":
            cases, workers = spec.get("cases", 1), spec.get("workers", 1)
            if type(cases) is not int or cases != 1 or type(workers) is not int or workers != 1:
                raise ValueError("Dashboard evaluation requires cases=1 and workers=1")
            run, checkpoint = spec.get("run"), spec.get("checkpoint")
            if bool(run) == bool(checkpoint):
                raise ValueError("Choose exactly one saved run or project-relative checkpoint")
            if run:
                if not isinstance(run, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run):
                    raise ValueError("Invalid saved run name")
                if self.fixture_script is None:
                    folder = (self.project_root / "results/transformer/train" / run).resolve(strict=True)
                    if not folder.is_relative_to(self.project_root) or not any((folder / p).is_file() for p in ("best.json", "latest.json")):
                        raise ValueError("Saved run checkpoint pointer is missing")
                args.extend(["--run", run])
                clean["run"] = run
            else:
                if not isinstance(checkpoint, str) or "\\" in checkpoint or Path(checkpoint).is_absolute() or ":" in checkpoint:
                    raise ValueError("Checkpoint must use a relative POSIX path")
                candidate = (self.project_root / checkpoint).resolve(strict=True)
                if not candidate.is_relative_to(self.project_root) or candidate.suffix != ".pt" or not candidate.is_file():
                    raise ValueError("Checkpoint must be an existing .pt file inside the project")
                relative = candidate.relative_to(self.project_root).as_posix()
                args.extend(["--checkpoint", relative])
                clean["checkpoint"] = relative
            args.extend(["--cases", "1", "--workers", "1"])
            clean.update(cases=1, workers=1)
        elif set(spec) - {"command"}:
            raise ValueError("This command accepts no dashboard parameters")
        return args, clean, script

    def start(self, spec):
        if not self.enabled:
            raise PermissionError("Research launcher is disabled; enable it explicitly in local configuration")
        with self._mutex:
            args, clean, script = self._command(spec)
            digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
            lock_path = self.storage_dir / ("command-" + digest + ".lock")
            try:
                lease = lock_path.open("x", encoding="utf-8")
            except FileExistsError:
                raise RuntimeError("An identical command is active or has unresolved ownership") from None
            job_id = uuid.uuid4().hex
            record = {"job_id": job_id, "execution_id": job_id, "status": "starting",
                      "command": clean, "command_sha256": digest,
                      "started_at": _now(), "completed_at": None, "exit_code": None,
                      "timestamp_scope": "launcher_parent_process_only",
                      "episode_started_at": None, "episode_completed_at": None,
                      "owner_nonce": self.owner_nonce, "pid": None,
                      "fixture": self.fixture_script is not None,
                      "label": "SYNTHETIC FIXTURE — no research executed" if self.fixture_script else "Explicit research CLI job",
                      "source_refs": [{"path": "fixture.py" if self.fixture_script else "transformer_run.py",
                                       "sha256": _sha(script)}],
                      "research_python_name": self.research_python.name,
                      "requested_work_complete": None,
                      "status_note": "Exit success does not establish completion of every requested scientific stage."}
            with lease:
                json.dump({"job_id": job_id, "owner_nonce": self.owner_nonce}, lease)
            _atomic(self._record_path(job_id), record)
            stdout = (self.storage_dir / ("job-" + job_id + ".log")).open("wb")
            process = None
            tree = None
            try:
                options = {"cwd": str(self.project_root), "stdin": subprocess.DEVNULL,
                           "stdout": stdout, "stderr": subprocess.STDOUT, "shell": False}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = subprocess.SW_HIDE
                    options["startupinfo"] = startup
                else:
                    options["start_new_session"] = True
                process = subprocess.Popen(args, **options)
                if os.name == "nt":
                    tree = _WindowsJob(process)
                record.update(status="running", pid=process.pid)
                _atomic(self._record_path(job_id), record)
                self._owned[job_id] = {"process": process, "tree": tree,
                                       "lock_path": lock_path, "nonce": self.owner_nonce,
                                       "stdout": stdout, "stopping": False}
                threading.Thread(target=self._watch, args=(job_id,), daemon=True).start()
                return dict(record)
            except BaseException as error:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                if tree is not None:
                    tree.close()
                stdout.close()
                record.update(status="failed_to_start", completed_at=_now(), error=type(error).__name__)
                _atomic(self._record_path(job_id), record)
                lock_path.unlink(missing_ok=True)
                raise

    def _watch(self, job_id):
        owned = self._owned[job_id]
        code = owned["process"].wait()
        with self._mutex:
            record = self.get_job(job_id)
            stopped = owned["stopping"]
            record.update(status="stopped" if stopped else ("succeeded" if code == 0 else "failed"),
                          exit_code=code, completed_at=_now(), requested_work_complete=None)
            _atomic(self._record_path(job_id), record)
            owned["stdout"].close()
            if owned["tree"]:
                owned["tree"].close()
            owned["lock_path"].unlink(missing_ok=True)
            self._owned.pop(job_id, None)
        if self.on_complete:
            try:
                self.on_complete(dict(record))
            except Exception:
                # Rescan is best effort. Raw research output and job outcome remain intact.
                record["rescan_notice"] = "Completion callback failed; manual rescan is available."
                _atomic(self._record_path(job_id), record)

    def stop(self, job_id, grace_seconds=5):
        with self._mutex:
            record = self.get_job(job_id)
            owned = self._owned.get(job_id)
            if owned is None:
                if record["status"] not in _ACTIVE:
                    return record
                raise PermissionError("This service instance does not own the process; refusing PID-based stop")
            if owned["nonce"] != self.owner_nonce or record.get("owner_nonce") != self.owner_nonce:
                raise PermissionError("Process ownership token differs")
            process = owned["process"]
            if process.poll() is not None:
                return record
            owned["stopping"] = True
            record["status"] = "stopping"
            record["stop_requested_at"] = _now()
            _atomic(self._record_path(job_id), record)
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGINT)
            except OSError:
                pass
            threading.Thread(target=self._force_stop, args=(job_id, max(0.1, float(grace_seconds))), daemon=True).start()
            return record

    def _force_stop(self, job_id, delay):
        time.sleep(delay)
        with self._mutex:
            owned = self._owned.get(job_id)
            if not owned or owned["process"].poll() is not None:
                return
            if os.name == "nt":
                owned["tree"].terminate()
            else:
                os.killpg(owned["process"].pid, signal.SIGKILL)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in bounded dashboard research wrapper. Training is disabled.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--storage-dir", required=True)
    parser.add_argument("--research-python", required=True)
    parser.add_argument("--enable-research", action="store_true", required=True)
    parser.add_argument("command", choices=ALLOWED_COMMANDS)
    parser.add_argument("--run")
    parser.add_argument("--checkpoint")
    args = parser.parse_args(argv)
    manager = JobManager(args.storage_dir, args.project_root, args.research_python, args.enable_research)
    spec = {"command": args.command}
    if args.run:
        spec["run"] = args.run
    if args.checkpoint:
        spec["checkpoint"] = args.checkpoint
    job = manager.start(spec)
    print(json.dumps(job, ensure_ascii=False), flush=True)
    try:
        while manager.get_job(job["job_id"])["status"] in _ACTIVE:
            time.sleep(.2)
    except KeyboardInterrupt:
        manager.stop(job["job_id"])
        while manager.get_job(job["job_id"])["status"] in _ACTIVE:
            time.sleep(.2)
    final = manager.get_job(job["job_id"])
    print(json.dumps(final, ensure_ascii=False), flush=True)
    return 0 if final["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
