"""Serialize one persisted simulation attempt across worker restarts."""
from pathlib import Path
import json
import sys

from .team_capture import main as capture_main, write_result
from .team_runtime import file_lock


def main():
    # The parent passes the result path as --result to the normal capture CLI.
    result = Path(sys.argv[sys.argv.index("--result") + 1])
    failure = result.with_name("failure.json")
    with file_lock(result.with_suffix(".lock"), wait=True):
        if result.is_file():
            return 0
        if failure.is_file():
            print(json.loads(failure.read_text(encoding="utf-8"))["message"], file=sys.stderr)
            return 1
        try:
            capture_main()
        except Exception as exc:
            write_result(failure, {"message": str(exc)[:1200]})
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
