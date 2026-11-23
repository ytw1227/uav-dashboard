"""Build a private runnable snapshot from a local team commit and the installed research data."""
from contextlib import contextmanager
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile

from .team_source import source_status


def research_runtime():
    value = os.environ.get("UAV_RESEARCH_CORE")
    if not value:
        raise ValueError("연구 실행 자료를 먼저 연결하세요. settings.local.json의 UAV_RESEARCH_CORE에 기존 연구 폴더를 지정하세요.")
    core = Path(value).resolve()
    if not (core / "code" / "team_portability.py").is_file():
        raise ValueError("연구 폴더에 code/team_portability.py가 없습니다. 전체 팀 배포 자료가 필요합니다.")
    python = os.environ.get("UAV_RESEARCH_PYTHON")
    metadata = core.parent / "runtime_environment.json"
    if not python and metadata.is_file():
        python = json.loads(metadata.read_text(encoding="utf-8")).get("executable")
    if not python or not Path(python).is_file():
        raise ValueError("기존 연구용 Python을 찾지 못했습니다. settings.local.json의 UAV_RESEARCH_PYTHON에 실행 파일을 지정하세요.")
    return core, Path(python).resolve()


@contextmanager
def file_lock(path, *, wait=False):
    """An OS lock releases on process exit, including an unexpected worker stop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fp:
        if not fp.seek(0, os.SEEK_END):
            fp.write(b"0")
            fp.flush()
        fp.seek(0)
        if os.name == "nt":
            import msvcrt
            lock = lambda: msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(fp, fcntl.LOCK_UN)
        while True:
            try:
                lock()
                break
            except OSError:
                if not wait:
                    yield False
                    return
                time.sleep(.2)
        try:
            yield True
        finally:
            fp.seek(0)
            unlock()


def _linked(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def native_path(path):
    """The team's nested installed map paths exceed MAX_PATH on Windows."""
    path = Path(path).resolve()
    raw = str(path)
    if os.name != "nt" or raw.startswith("\\\\?\\"):
        return path
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw[2:])
    return Path("\\\\?\\" + raw)


def _copy_release_assets(core, target, deleted):
    base = core / "code"
    ignored = {"__pycache__", ".git", ".venv", ".pytest_cache", "node_modules"}
    def ignore(directory, names):
        root = Path(directory)
        skip = {name for name in names if name in ignored or name.endswith((".pyc", ".pyo"))}
        skip.update(name for name in names if (root / name).relative_to(base).as_posix() in deleted)
        # These two team bootstrap helpers are shipped only in the full research release.
        skip.update(name for name in names if name.endswith(".py") and
                    not (root == base and name in {"team_portability.py", "run_latest_final_v1.py"}))
        if root == base:
            skip.add("outputs")
        if any(_linked(root / name) for name in set(names) - skip):
            raise ValueError("연구 자료에 연결 파일이 있어 안전한 복사본을 준비할 수 없습니다.")
        return skip
    # Private copies also isolate team code which updates a map cache during a flight.
    shutil.copytree(base, target, ignore=ignore)


def _overlay_commit(source, sha, code, zip_path):
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    with zip_path.open("wb") as dest:
        result = subprocess.run(["git", "-C", str(source), "archive", "--format=zip", sha],
                                env=env, stdout=dest, stderr=subprocess.PIPE, timeout=300)
    if result.returncode:
        raise ValueError("로컬 팀 저장소에서 선택한 커밋을 읽지 못했습니다. 팀 코드 갱신 상태를 확인하세요.")
    with zipfile.ZipFile(zip_path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            rel = PurePosixPath(entry.filename)
            if (rel.is_absolute() or ".." in rel.parts or "\\" in entry.filename or
                    any(":" in part or part.casefold() == ".git" for part in rel.parts) or
                    stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("팀 스냅샷에 지원하지 않는 링크 또는 파일 이름이 있습니다.")
            out = code.joinpath(*rel.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as src, out.open("wb") as dest:
                shutil.copyfileobj(src, dest)


def _git_bytes(source, *args):
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    return subprocess.check_output(["git", "-C", str(source), *args], env=env, timeout=60)


def _inventory(source, sha):
    if _git_bytes(source, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError("팀 저장소의 전체 이력이 필요합니다. 전체 복제본을 팀 소스 폴더에 연결하세요.")
    listing = _git_bytes(source, "ls-tree", "-rz", sha)
    blobs = {}
    for entry in listing.split(b"\0"):
        if entry:
            header, name = entry.split(b"\t", 1)
            blobs[name.decode("utf-8")] = header.split()[-1].decode("ascii")
    removed = _git_bytes(source, "log", "--full-history", "-m", "--no-renames", "--diff-filter=D",
                         "--name-only", "--format=", "-z", sha)
    # A deleted Git path must never reappear from the older release overlay.
    return blobs, {p.decode("utf-8") for p in removed.split(b"\0") if p} - blobs.keys()


def _verify_active_model(code, source, blobs):
    prefix = "final_v1/halfsec_main/"
    if prefix + "release.py" not in blobs:
        return  # the capture adapter checks whether this commit has a supported entry point
    key = prefix + "checkpoint.json"
    if key not in blobs:
        raise ValueError("팀 커밋에 모델 식별 정보(checkpoint.json)가 없어 설치된 가중치를 검증할 수 없습니다.")
    model_info = _git_bytes(source, "cat-file", "blob", blobs[key])
    if not (code / key).is_file() or (code / key).read_bytes() != model_info:
        raise ValueError("준비된 모델 정보가 선택한 팀 커밋과 다릅니다.")
    expected = json.loads(model_info).get("sha256")
    weights = code / prefix / "checkpoint.pt"
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or not weights.is_file():
        raise ValueError("팀 커밋에 대응하는 모델 파일 또는 SHA-256을 확인할 수 없습니다.")
    with weights.open("rb") as fp:
        actual = hashlib.file_digest(fp, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("설치된 모델 가중치가 선택한 팀 커밋의 SHA-256과 다릅니다. 해당 버전의 팀 가중치를 연결하세요.")


def map_coverage(source, sha, core):
    """Preflight the same registry order and map identity required by team_capture."""
    base = native_path(Path(core) / "code")
    blobs, deleted = _inventory(source, sha)

    def read(relative):
        if relative in deleted:
            return None
        if relative in blobs:
            return _git_bytes(source, "cat-file", "blob", blobs[relative])
        path = base / relative
        return path.read_bytes() if path.is_file() else None

    def registry(relative):
        data = read(relative)
        return {r["key"]: r for r in json.loads(data)["maps"]} if data else {}

    primary = registry("fast_halfsec/artifacts/map_registry.json")
    secondary = registry("final_v1/halfsec_expanded/artifacts/map_registry.json")
    # Read the installed team's literal study-role guard. A later version can
    # expand the cohort without keeping the previous release's roles in the UI.
    # If the guard changes shape, final eligibility is checked during the flight.
    roles = None
    context = read("fast_halfsec/context.py") if "fast_halfsec/context.py" in blobs else None
    if context:
        tree = ast.parse(context)
        for method in tree.body:
            if not isinstance(method, ast.FunctionDef) or method.name != "survey_for":
                continue
            for stmt in method.body:
                if not isinstance(stmt, ast.If) or not any(isinstance(s, ast.Raise) for s in stmt.body):
                    continue
                cmp = stmt.test
                if (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1 and isinstance(cmp.ops[0], ast.NotIn)
                        and isinstance(cmp.left, ast.Subscript) and isinstance(cmp.left.slice, ast.Constant)
                        and cmp.left.slice.value == "study_role" and isinstance(cmp.comparators[0], (ast.Tuple, ast.List, ast.Set))
                        and all(isinstance(i, ast.Constant) and isinstance(i.value, str) for i in cmp.comparators[0].elts)):
                    roles = {i.value for i in cmp.comparators[0].elts}
                    break
    paths = read("handoff_paths.json")
    old = json.loads(paths)["source_root"].replace("\\", "/").rstrip("/") if paths else ""
    maps = json.loads((Path(core) / "dashboard_execution/selected_maps.json").read_text(encoding="utf-8"))["maps"]
    result = {}
    for card in maps:
        region = card["key"]
        entry = primary.get(region) or secondary.get(region)
        reason = ""
        if not entry:
            reason = "팀 시뮬레이터의 지도 목록에 없는 지역"
        elif roles is not None and entry.get("study_role") not in roles:
            reason = f"팀 시뮬레이터에서 실행을 허용하지 않는 지도 ({entry.get('study_role', '역할 미기록')})"
        elif not card.get("sha256") or entry.get("sha256") != card["sha256"]:
            reason = "팀 지도와 기존 지역 카드의 지도 식별값이 다릅니다."
        else:
            path = entry["path"].replace("\\", "/")
            if old and path.casefold().startswith(old.casefold() + "/"):
                relative = path[len(old) + 1:]
            else:
                relative = "drone_forecast_joint_training/" + path
            if (":" in relative or relative.startswith("/") or ".." in PurePosixPath(relative).parts or
                    not native_path(base / relative).is_relative_to(base)):
                reason = "팀 지도 경로를 현재 연구 자료에 연결할 수 없습니다."
            else:
                contents = read(relative)
                if contents is None or hashlib.sha256(contents).hexdigest() != card["sha256"]:
                    reason = "팀 지도 파일이 없거나 실제 파일의 식별값이 다릅니다."
        result[region] = {"eligible": not reason, "reason": reason, "name": card.get("name", region)}
    return result


def prepare_version(source, sha, core, state_root):
    """Use the selected local object even if another user syncs main during a queue."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("유효한 팀 커밋을 선택하세요.")
    source, core = Path(source).resolve(), Path(core).resolve()
    source_status(source)  # fixed team origin; the selected commit may precede the new HEAD
    root = native_path(Path(state_root) / "versions")
    root.mkdir(parents=True, exist_ok=True)
    identity = {"sha": sha, "release_root": str(core), "format": 1}
    folder = root / sha
    blobs, deleted = _inventory(source, sha)

    def reusable(location):
        marker = location / "snapshot.json"
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != identity:
            return False
        code = location / "code"
        if any((code / name).is_file() for name in deleted):
            return False
        _verify_active_model(code, source, blobs)
        return True

    with file_lock(root / f"{sha}.lock", wait=True):
        if folder.is_dir():
            if reusable(folder):
                return folder / "code"
            # A different installation may use the same local cache.
            suffix = hashlib.sha256(str(core).encode()).hexdigest()[:16]
            folder = root / f"{sha}-{suffix}"
            if reusable(folder):
                return folder / "code"
            if folder.exists():
                raise ValueError("연구 자료 캐시의 출처를 확인할 수 없습니다.")
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as tmp:
            staging = Path(tmp) / "snapshot"
            staging.mkdir()
            code = staging / "code"
            _copy_release_assets(native_path(core), code, deleted)
            _overlay_commit(source, sha, code, Path(tmp) / "team.zip")
            _verify_active_model(code, source, blobs)
            (staging / "snapshot.json").write_text(json.dumps(identity, ensure_ascii=False), encoding="utf-8")
            staging.rename(folder)
    return folder / "code"
