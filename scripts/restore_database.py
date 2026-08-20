#!/usr/bin/env python3
import argparse,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.config import get_settings
p=argparse.ArgumentParser();p.add_argument("backup");p.add_argument("--confirm",action="store_true");a=p.parse_args()
if not a.confirm:raise SystemExit("Restore not performed. Re-run with --confirm after stopping CaseVault.")
src=Path(a.backup);dest=Path(get_settings().database_url.removeprefix("sqlite:///./"))
if not src.is_file():raise SystemExit("Backup does not exist")
with sqlite3.connect(src) as s:
    if s.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise SystemExit("Backup integrity check failed")
    with sqlite3.connect(dest) as d:s.backup(d)
print(f"Restored {dest}")
