#!/usr/bin/env python3
import argparse,sqlite3,sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.config import get_settings
p=argparse.ArgumentParser();p.add_argument("--output-dir",default="backups");a=p.parse_args()
src=Path(get_settings().database_url.removeprefix("sqlite:///./"));out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True);dest=out/f"casevault_backup_{datetime.now():%Y-%m-%d_%H%M%S}.db"
with sqlite3.connect(src) as s,sqlite3.connect(dest) as d:s.backup(d)
print(dest)
