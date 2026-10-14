"""Build submission/AgentSprint_<TeamName>.zip from git-tracked files only (+ latest outbox.json).

Usage: python make_submission.py [TeamName]
"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

EXCLUDE = re.compile(
    r"(^|/)(\.env$|\.env\.(?!example$)|\.venv/|venv/|credentials/|logs/|__pycache__/|\.pytest|submission/|team_kit/)"
    r"|\.(sqlite3?|db|pyc|docx|pdf)$|(^|/)(token|client_secret|credentials)[^/]*\.json$"
)
SECRET = re.compile(rb"AIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])")  # real Google API key shape


def main() -> int:
    team = sys.argv[1] if len(sys.argv) > 1 else "TeamName"
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    if Path("outbox.json").exists():
        files.append("outbox.json")
    files = sorted({f for f in files if Path(f).is_file() and not EXCLUDE.search(f)})
    out = Path("submission") / f"AgentSprint_{team}.zip"
    out.parent.mkdir(exist_ok=True)
    leaks = [f for f in files if SECRET.search(Path(f).read_bytes())]
    if leaks:
        print(f"REFUSING: these files look like they contain an API key: {leaks}")
        return 1
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f"AgentSprint_{team}/{f}", Path(f).read_bytes())
    print(f"Wrote {out} ({len(files)} files, {out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
