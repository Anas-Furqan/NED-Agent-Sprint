"""Build submission/AgentSprint_<TeamName>.zip from git-tracked files (+ latest outbox.json / run_trace.json).

Usage: python make_submission.py [TeamName] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

EXCLUDE = re.compile(
    r"(^|/)(\.env$|\.env\.(?!example$)|\.venv/|venv/|credentials/|logs/|__pycache__/|\.pytest|submission/|team_kit/|"
    r"\.cache/|\.streamlit/secrets\.toml)"
    r"|\.(sqlite3?|db|pyc|docx|pdf)$|(^|/)(token|client_secret|credentials)[^/]*\.json$"
)
SECRET = re.compile(rb"AIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])")  # real Google API key shape
GENERATED = ("outbox.json", "run_trace.json")  # results of the last run, useful for the reviewers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("team", nargs="?", default="TeamName")
    parser.add_argument("--out-dir", default="submission", type=Path)
    args = parser.parse_args()
    git = ["git", "ls-files"]
    files = subprocess.run(git, capture_output=True, text=True, check=True).stdout.splitlines()  # noqa: S603
    files += [f for f in GENERATED if Path(f).exists()]
    files = sorted({f for f in files if Path(f).is_file() and not EXCLUDE.search(f)})
    leaks = [f for f in files if SECRET.search(Path(f).read_bytes())]
    if leaks:
        print(f"REFUSING: these files look like they contain an API key: {leaks}")
        return 1
    out = args.out_dir / f"AgentSprint_{args.team}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f"AgentSprint_{args.team}/{f}", Path(f).read_bytes())
    print(f"Wrote {out} ({len(files)} files, {out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
