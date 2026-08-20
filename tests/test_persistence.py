from backend.services.case_service import find_patient, save_visit, duplicate_visit
from copy import deepcopy

PAYLOAD={
    "patient":{"medical_record_number":"00.00.01","full_name":"Pasien Contoh","title":None,"sex":None,"age":None,"age_unit":None,"insurance":None,"hospital":"RS Pendidikan","raw_header":"Synthetic test"},
    "visit":{"visit_date":"2026-08-19","visit_type":"Outpatient","pod_number":None,"pod_roman":None,"location":None,"hospital":"RS Pendidikan","general_condition":None,"consciousness":None,"blood_pressure_systolic":None,"blood_pressure_diastolic":None,"heart_rate":None,"respiratory_rate":None,"temperature_celsius":None,"spo2_percent":None,"oxygen_support":None,"extraoral":None,"intraoral":None},
    "soap":{"subjective":"Synthetic test","objective_raw":"Synthetic test","assessment":"Synthetic test","plan":"Synthetic test","proposal":None,"original_soap":"Synthetic nonclinical test"},
    "procedures":[],"diagnoses":[],"residents":[],"dpjp":None,
}

def test_same_rm_reuses_patient(db):
    d=deepcopy(PAYLOAD);p1,e1,v1=save_visit(db,d)
    assert find_patient(db,"00-00-01").id==p1.id
    d2=deepcopy(PAYLOAD);d2["visit"]["visit_date"]="2026-08-20";d2["episode_id"]=e1.id
    p2,e2,v2=save_visit(db,d2);assert p2.id==p1.id and e2.id==e1.id
def test_duplicate_visit_detected(db):
    d=deepcopy(PAYLOAD);_,episode,_=save_visit(db,d);d["episode_id"]=episode.id
    try:save_visit(db,d);assert False
    except ValueError as e:assert "duplicate" in str(e).lower()
