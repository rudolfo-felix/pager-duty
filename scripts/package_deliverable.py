#!/usr/bin/env python3
"""Run tests and build a clean ZIP of the PagerDuty take-home."""
from __future__ import annotations
import argparse, hashlib, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"agent.py", "demo.py", "README.md", "PRODUCTION_DISCUSSION.md"}
EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", "runtime", "dist"}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/pagerduty_sre_agent_takehome.zip")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    absent = sorted(name for name in REQUIRED if not (ROOT / name).is_file())
    if absent:
        raise SystemExit("Missing files: " + ", ".join(absent))
    if not args.skip_tests:
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in ROOT.rglob("*") if p.is_file()
                   and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts)
                   and p.resolve() != output.resolve()
                   and p.suffix not in {".pyc", ".db", ".sqlite", ".sqlite3"})
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as zf:
        bad = zf.testzip()
        names = set(zf.namelist())
        if bad or not REQUIRED.issubset(names):
            raise SystemExit(f"ZIP validation failed: {bad or 'required files missing'}")
    print(f"Created {output} ({output.stat().st_size} bytes)")
    print("SHA256:", hashlib.sha256(output.read_bytes()).hexdigest())
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
