"""Exercise team source updates against a real, offline Git remote."""

from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nicegui_dashboard import team_source


def git(directory, *args):
    command = [
        "git", "-c", "user.name=Dashboard Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "-C", str(directory), *args,
    ]
    return subprocess.run(
        command, check=True, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30,
    ).stdout.strip()


@unittest.skipUnless(shutil.which("git"), "Git is required for the team source workflow")
class TeamSourceTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="dashboard-team-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.origin = self.root / "team.git"
        self.author = self.root / "author"
        self.checkout = self.root / "independent checkout"
        git(self.root, "init", "--bare", "-b", "main", str(self.origin))
        git(self.root, "init", "-b", "main", str(self.author))
        git(self.author, "remote", "add", "origin", str(self.origin.resolve()))
        self.initial = self.publish("first")
        remote = patch.object(team_source, "TEAM_URL", str(self.origin.resolve()))
        remote.start()
        self.addCleanup(remote.stop)

    def publish(self, text):
        source = self.author / "final_v1" / "fly.py"
        source.parent.mkdir(exist_ok=True)
        source.write_text(f"# {text}\n", encoding="utf-8")
        git(self.author, "add", "--", "final_v1/fly.py")
        git(self.author, "commit", "-m", text)
        git(self.author, "push", "origin", "main")
        return git(self.author, "rev-parse", "HEAD")

    def commit_local_work(self):
        note = self.checkout / "note.txt"
        note.write_text("my local work", encoding="utf-8")
        git(self.checkout, "add", "--", "note.txt")
        git(self.checkout, "commit", "-m", "local work")
        return git(self.checkout, "rev-parse", "HEAD")

    def test_initial_clone_fast_forward_and_repeat(self):
        first = team_source.sync_source(self.checkout)
        self.assertTrue(first["changed"])
        self.assertEqual((first["commit"], first["branch"]), (self.initial, "main"))
        self.assertEqual(team_source.source_status(self.checkout)["commit"], self.initial)

        updated = self.publish("new team control")
        second = team_source.sync_source(self.checkout)
        self.assertTrue(second["changed"])
        self.assertEqual((second["commit"], second["branch"]), (updated, "main"))
        self.assertEqual((self.checkout / "final_v1/fly.py").read_text(encoding="utf-8"),
                         "# new team control\n")
        repeat = team_source.sync_source(self.checkout)
        self.assertFalse(repeat["changed"])
        self.assertEqual(repeat["commit"], updated)

    def test_existing_non_repository_files_survive(self):
        self.checkout.mkdir()
        note = self.checkout / "note.txt"
        note.write_text("keep", encoding="utf-8")
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(note.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.checkout / ".git").exists())

    def test_rejects_uncommitted_work(self):
        team_source.sync_source(self.checkout)
        self.publish("upstream moved")
        source = self.checkout / "final_v1/fly.py"
        source.write_text("# my local edit\n", encoding="utf-8")
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(source.read_text(encoding="utf-8"), "# my local edit\n")

    def test_rejects_local_commits_even_when_upstream_is_behind(self):
        team_source.sync_source(self.checkout)
        local = self.commit_local_work()
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), local)
        self.publish("upstream diverged")
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), local)
        self.assertEqual((self.checkout / "note.txt").read_text(encoding="utf-8"), "my local work")

    def test_rejects_wrong_branch_and_wrong_origin(self):
        team_source.sync_source(self.checkout)
        git(self.checkout, "switch", "-c", "personal")
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "personal")

        git(self.checkout, "switch", "main")
        different = str((self.root / "other.git").resolve())
        git(self.checkout, "remote", "set-url", "origin", different)
        with self.assertRaises(team_source.TeamSourceError):
            team_source.sync_source(self.checkout)
        self.assertEqual(git(self.checkout, "remote", "get-url", "origin"), different)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.initial)
