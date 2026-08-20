#!/usr/bin/env python3
import argparse,csv,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.database import SessionLocal
from backend.models import Patient
p=argparse.ArgumentParser();p.add_argument("--output",default="patients_export.csv");a=p.parse_args()
with SessionLocal() as db,open(a.output,"w",newline="",encoding="utf-8") as f:
    w=csv.writer(f);w.writerow(["id","rm","name","sex","insurance","hospital"])
    for p in db.query(Patient):w.writerow([p.id,p.medical_record_number,p.full_name,p.sex,p.insurance,p.primary_hospital])
print(a.output)
