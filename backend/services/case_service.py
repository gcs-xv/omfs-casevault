from __future__ import annotations
import json
from datetime import date
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from backend.models import AuditLog, Clinician, Diagnosis, Episode, Media, Patient, Procedure, Visit
from backend.utils.normalization import normalize_rm, normalize_text

def find_patient(db:Session,rm:str|None)->Patient|None:
    normalized=normalize_rm(rm)
    return db.scalar(select(Patient).where(Patient.medical_record_number_normalized==normalized)) if normalized else None

def get_or_create_clinician(db:Session,name:str,role:str)->Clinician:
    key=normalize_text(name); obj=db.scalar(select(Clinician).where(Clinician.normalized_name==key))
    if not obj: obj=Clinician(full_name=name,normalized_name=key,role=role); db.add(obj); db.flush()
    return obj

def score_episode(ep:Episode,procedures:list[str],diagnoses:list[str])->tuple[int,list[str]]:
    score=0; why=[]; target={normalize_text(x) for x in procedures}; known={x.normalized_name for x in ep.procedures}
    if target & known: score+=40; why.append("same procedure +40")
    words=set(normalize_text(" ".join(diagnoses)).split()); existing=set(normalize_text(ep.primary_diagnosis or ep.title).split())
    if len(words & existing)>=2: score+=30; why.append("diagnosis keywords +30")
    if ep.status in {"Active","Follow-up"}:score+=15;why.append("active episode +15")
    return score,why

def suggest_episodes(db:Session,patient_id:str,procedures:list[str],diagnoses:list[str])->list[dict]:
    eps=db.scalars(select(Episode).where(Episode.patient_id==patient_id).order_by(Episode.created_at.desc())).all()
    return sorted(({"id":e.id,"episode_number":e.episode_number,"title":e.title,"score":score_episode(e,procedures,diagnoses)[0],"reason":score_episode(e,procedures,diagnoses)[1]} for e in eps),key=lambda x:x["score"],reverse=True)

def duplicate_visit(db:Session,patient_id:str,episode_id:str,visit_date:date,visit_type:str,pod_number:int|None)->Visit|None:
    return db.scalar(select(Visit).where(Visit.patient_id==patient_id,Visit.episode_id==episode_id,Visit.visit_date==visit_date,Visit.visit_type==visit_type,Visit.pod_number==pod_number,Visit.is_deleted.is_(False)))

def save_visit(db:Session,payload:dict,actor:str="local-user") -> tuple[Patient,Episode,Visit]:
    p=payload["patient"]; v=payload["visit"]; soap=payload["soap"]
    if not normalize_rm(p.get("medical_record_number")): raise ValueError("A valid medical record number is required")
    patient=find_patient(db,p.get("medical_record_number"))
    if not patient:
        patient=Patient(medical_record_number=p["medical_record_number"],medical_record_number_normalized=normalize_rm(p["medical_record_number"]),title=p.get("title"),full_name=p.get("full_name") or "Unknown",sex=p.get("sex"),age_at_first_record=p.get("age"),age_unit=p.get("age_unit"),insurance=p.get("insurance"),primary_hospital=p.get("hospital"),created_by=actor);db.add(patient);db.flush()
        db.add(AuditLog(user_id=actor,action="create",entity_type="patient",entity_id=patient.id,after_json=json.dumps({"rm":patient.medical_record_number,"name":patient.full_name})))
    episode=None
    if payload.get("episode_id"): episode=db.get(Episode,payload["episode_id"])
    if not episode:
        suggestions=suggest_episodes(db,patient.id,payload.get("procedures",[]),payload.get("diagnoses",[]))
        if suggestions and suggestions[0]["score"]>=40: episode=db.get(Episode,suggestions[0]["id"])
    if not episode:
        count=db.scalar(select(func.count()).select_from(Episode).where(Episode.patient_id==patient.id)) or 0
        title=(payload.get("diagnoses") or payload.get("procedures") or ["Clinical Episode"])[-1]
        episode=Episode(patient_id=patient.id,episode_number=f"EP{count+1:02d}",title=title,primary_diagnosis="\n".join(payload.get("diagnoses",[])) or None,start_date=date.fromisoformat(v["visit_date"]),hospital=v.get("hospital"),created_by=actor);db.add(episode);db.flush()
    for name in payload.get("procedures",[]):
        key=normalize_text(name); obj=db.scalar(select(Procedure).where(Procedure.normalized_name==key))
        if not obj: obj=Procedure(name=name,normalized_name=key);db.add(obj);db.flush()
        if obj not in episode.procedures:episode.procedures.append(obj)
    for name in payload.get("diagnoses",[]):
        key=normalize_text(name); obj=db.scalar(select(Diagnosis).where(Diagnosis.normalized_name==key))
        if not obj:obj=Diagnosis(name=name,normalized_name=key);db.add(obj);db.flush()
        if obj not in episode.diagnoses:episode.diagnoses.append(obj)
    dpjp=get_or_create_clinician(db,payload["dpjp"]["full_name"],"DPJP") if payload.get("dpjp") else None
    visit_date=date.fromisoformat(v["visit_date"])
    if duplicate_visit(db,patient.id,episode.id,visit_date,v["visit_type"],v.get("pod_number")): raise ValueError("Possible duplicate visit; choose the existing visit or explicitly create a separate event")
    fields={k:v.get(k) for k in ["visit_type","pod_number","pod_roman","location","hospital","general_condition","consciousness","blood_pressure_systolic","blood_pressure_diastolic","heart_rate","respiratory_rate","temperature_celsius","spo2_percent","oxygen_support","extraoral","intraoral"]}
    visit=Visit(patient_id=patient.id,episode_id=episode.id,visit_date=visit_date,subjective=soap.get("subjective"),objective_raw=soap.get("objective_raw"),assessment=soap.get("assessment"),plan=soap.get("plan"),proposal=soap.get("proposal"),original_soap=soap["original_soap"],raw_header=p.get("raw_header"),dpjp_id=dpjp.id if dpjp else None,created_by=actor,save_state="uploading",**fields);db.add(visit);db.flush()
    for name in payload.get("residents",[]):visit.residents.append(get_or_create_clinician(db,name,"Resident"))
    db.add(AuditLog(user_id=actor,action="create",entity_type="visit",entity_id=visit.id,after_json=json.dumps({"date":v["visit_date"],"pod":v.get("pod_number")})));db.commit()
    return patient,episode,visit

def search_cases(db:Session,q:str,limit:int=50)->list[Patient]:
    term=f"%{q.strip()}%"; rm=normalize_rm(q)
    stmt=select(Patient).distinct().outerjoin(Patient.episodes).outerjoin(Episode.visits).outerjoin(Episode.procedures).outerjoin(Episode.diagnoses).where(or_(Patient.full_name.ilike(term),Patient.medical_record_number.ilike(term),Patient.medical_record_number_normalized==rm if rm else False,Episode.title.ilike(term),Episode.primary_diagnosis.ilike(term),Procedure.name.ilike(term),Diagnosis.name.ilike(term))).limit(limit)
    return list(db.scalars(stmt).unique())
