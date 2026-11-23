"""Keep a user-controlled checkout of the team's public simulator source."""
from pathlib import Path
import os
import subprocess
import tempfile


TEAM_URL = "https://github.com/MinchanKim1215/capstone-final-v1.git"
BRANCH = "main"


class TeamSourceError(RuntimeError):
    pass


def source_path():
    default = Path(__file__).resolve().parents[2] / "team-simulator"
    return Path(os.environ.get("UAV_TEAM_SOURCE") or default).resolve()


def _git(directory, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    # Keep checkout operations inside the selected repository even if another
    # Git command set repository-scoped variables in the caller's environment.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", "-c", "credential.interactive=false", *args],
            cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except FileNotFoundError as exc:
        raise TeamSourceError("Git을 찾을 수 없습니다. Git for Windows 설치를 확인하세요.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TeamSourceError("Git 응답 시간이 초과됐습니다. 연결을 확인한 뒤 다시 실행하세요.") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise TeamSourceError(detail or "Git 명령이 완료되지 않았습니다.")
    return result.stdout.strip()


def source_status(path):
    path = Path(path).resolve()
    if not (path / ".git").exists():
        raise TeamSourceError("연결된 팀 소스가 없습니다. ‘팀 코드 갱신’을 실행하세요.")
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise TeamSourceError("연결 경로가 팀 저장소의 최상위 폴더인지 확인하세요.")
    if _git(path, "remote", "get-url", "origin").removesuffix(".git") != TEAM_URL.removesuffix(".git"):
        raise TeamSourceError("이 폴더는 지정된 팀 GitHub 저장소와 연결되어 있지 않습니다.")
    branch = _git(path, "branch", "--show-current")
    if branch != BRANCH:
        raise TeamSourceError(f"팀 소스가 {BRANCH} 브랜치에 있지 않습니다.")
    sha = _git(path, "rev-parse", "--verify", "HEAD")
    return {"path": str(path), "branch": branch, "commit": sha, "repository": TEAM_URL}


def sync_source(path):
    path = Path(path).resolve()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # A partial download must not become the dashboard's source folder.
        with tempfile.TemporaryDirectory(dir=path.parent, prefix=".team-download-") as scratch:
            checkout = Path(scratch) / "checkout"
            _git(scratch, "clone", "--single-branch", "--no-tags", "--branch", BRANCH,
                 "--", TEAM_URL, str(checkout))
            source_status(checkout)
            if path.exists():
                raise TeamSourceError("다른 갱신 작업이 폴더를 만들었습니다. 다시 실행하세요.")
            checkout.rename(path)
        return {**source_status(path), "changed": True}

    before = source_status(path)
    if _git(path, "status", "--porcelain", "--untracked-files=all"):
        raise TeamSourceError("팀 소스 폴더에 로컬 변경이 있어 갱신을 멈췄습니다. 변경 내용을 먼저 확인하세요.")
    ref = f"refs/remotes/origin/{BRANCH}"
    _git(path, "fetch", "--no-tags", "origin", f"refs/heads/{BRANCH}:{ref}")
    remote = _git(path, "rev-parse", "--verify", ref)
    if before["commit"] != remote:
        base = _git(path, "merge-base", "HEAD", remote)
        if base != before["commit"]:
            raise TeamSourceError("로컬과 팀 저장소의 이력이 다릅니다. 기존 코드를 보존하고 중단했습니다.")
        _git(path, "merge", "--ff-only", remote)
    after = source_status(path)
    if after["commit"] != remote:
        raise TeamSourceError("로컬 버전과 가져온 팀 버전이 일치하지 않습니다. 다시 확인하세요.")
    return {**after, "changed": before["commit"] != after["commit"]}
