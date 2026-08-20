from __future__ import annotations
import io
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from PIL import Image
from backend.core.config import get_settings
from backend.core.database import Base, engine, get_db
from backend.models import Episode, Media, Patient, Visit
from backend.schemas import ParseRequest, SaveRequest
from backend.services.case_service import find_patient, save_visit, search_cases, suggest_episodes
from backend.services.drive_service import DriveService, credentials_from_session
from backend.services.soap_parser import parse_soap
from backend.utils.normalization import safe_name

settings=get_settings(); logging.basicConfig(filename="data/casevault.log",level=logging.INFO)
oauth=OAuth()
ACTIVE_SESSIONS:dict[str,dict]={}
if settings.google_client_id:
    oauth.register(name="google",client_id=settings.google_client_id,client_secret=settings.google_client_secret,server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",client_kwargs={"scope":"openid email profile https://www.googleapis.com/auth/drive.file"})

@asynccontextmanager
async def lifespan(_:FastAPI):
    Path("data").mkdir(exist_ok=True);Path("cache").mkdir(exist_ok=True);Base.metadata.create_all(engine);yield

app=FastAPI(title=settings.app_name,lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=settings.session_secret,https_only=settings.environment=="production",same_site="lax",max_age=8*60*60)
app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:8501","http://localhost:8501"],allow_credentials=True,allow_methods=["GET","POST","PATCH"],allow_headers=["*"])

@app.middleware("http")
async def headers(request:Request,call_next):
    response=await call_next(request);response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Cache-Control":"no-store"});return response

def current_user(request:Request)->dict:
    if settings.auth_disabled and settings.environment=="development": return {"email":"local-user","name":"Local User"}
    bearer=request.headers.get("authorization","")
    if bearer.startswith("Bearer ") and bearer[7:] in ACTIVE_SESSIONS:
        session=ACTIVE_SESSIONS[bearer[7:]];request.state.google_token=session["google_token"];return session["user"]
    user=request.session.get("user")
    if not user: raise HTTPException(401,"Authentication required")
    if settings.allowlist and user.get("email","").lower() not in settings.allowlist: raise HTTPException(403,"This Google account is not authorized")
    return user

@app.exception_handler(ValueError)
async def value_error(_:Request,exc:ValueError): return JSONResponse({"detail":str(exc)},status_code=400)

@app.get("/health")
def health(): return {"status":"ok","auth_configured":bool(settings.google_client_id),"drive_configured":bool(settings.google_drive_root_folder_id)}

@app.get("/auth/login")
async def login(request:Request):
    if not settings.google_client_id: raise HTTPException(503,"Google OAuth is not configured")
    return await oauth.google.authorize_redirect(request,settings.google_redirect_uri)

@app.get("/auth/callback")
async def callback(request:Request):
    token=await oauth.google.authorize_access_token(request);info=token.get("userinfo") or await oauth.google.userinfo(token=token)
    if settings.allowlist and info["email"].lower() not in settings.allowlist: raise HTTPException(403,"This Google account is not authorized")
    user={"email":info["email"],"name":info.get("name",info["email"])};request.session["user"]=user;token.update({"client_id":settings.google_client_id,"client_secret":settings.google_client_secret});request.session["google_token"]=token
    # One-time random bridge lets the separate local Streamlit process call the
    # API without exposing the Google token to browser-visible state.
    bridge=secrets.token_urlsafe(32);ACTIVE_SESSIONS[bridge]={"user":user,"google_token":token}
    return RedirectResponse(f"http://127.0.0.1:8501/?session_token={bridge}")

@app.get("/auth/me")
def me(user=Depends(current_user)):return user

@app.post("/parser/soap")
def parser(body:ParseRequest): return parse_soap(body.soap)

@app.post("/visits")
def create_visit(body:SaveRequest,request:Request,db:Session=Depends(get_db)):
    user=current_user(request);patient,episode,visit=save_visit(db,body.model_dump(),user["email"])
    return {"patient_id":patient.id,"episode_id":episode.id,"visit_id":visit.id,"save_state":visit.save_state}

@app.get("/patients/by-rm/{rm}")
def patient_by_rm(rm:str,db:Session=Depends(get_db),user=Depends(current_user)):
    p=find_patient(db,rm)
    if not p:return {"found":False}
    return {"found":True,"id":p.id,"full_name":p.full_name,"medical_record_number":p.medical_record_number,"episode_candidates":suggest_episodes(db,p.id,[],[])}

@app.get("/patients")
def patients(page:int=1,size:int=30,db:Session=Depends(get_db),user=Depends(current_user)):
    rows=db.scalars(select(Patient).order_by(Patient.updated_at.desc()).offset((page-1)*size).limit(min(size,100))).all()
    return [{"id":p.id,"name":p.full_name,"rm":p.medical_record_number,"sex":p.sex,"hospital":p.primary_hospital} for p in rows]

@app.get("/patients/{patient_id}")
def patient_detail(patient_id:str,db:Session=Depends(get_db),user=Depends(current_user)):
    p=db.scalar(select(Patient).options(selectinload(Patient.episodes).selectinload(Episode.visits).selectinload(Visit.media)).where(Patient.id==patient_id))
    if not p:raise HTTPException(404,"Patient not found")
    return {"id":p.id,"name":p.full_name,"rm":p.medical_record_number,"sex":p.sex,"insurance":p.insurance,"hospital":p.primary_hospital,"episodes":[{"id":e.id,"number":e.episode_number,"title":e.title,"status":e.status,"visits":[{"id":v.id,"date":v.visit_date.isoformat(),"type":v.visit_type,"pod":v.pod_number,"photos":len([m for m in v.media if not m.is_deleted])} for v in sorted(e.visits,key=lambda x:x.visit_date,reverse=True) if not v.is_deleted]} for e in p.episodes]}

@app.get("/visits/{visit_id}")
def visit_detail(visit_id:str,db:Session=Depends(get_db),user=Depends(current_user)):
    v=db.scalar(select(Visit).options(selectinload(Visit.media),selectinload(Visit.residents)).where(Visit.id==visit_id))
    if not v:raise HTTPException(404,"Visit not found")
    return {"id":v.id,"patient_id":v.patient_id,"episode_id":v.episode_id,"date":v.visit_date.isoformat(),"type":v.visit_type,"pod":v.pod_number,"pod_roman":v.pod_roman,"subjective":v.subjective,"objective":v.objective_raw,"assessment":v.assessment,"plan":v.plan,"proposal":v.proposal,"extraoral":v.extraoral,"intraoral":v.intraoral,"original_soap":v.original_soap,"drive_url":v.drive_folder_url,"save_state":v.save_state,"residents":[r.full_name for r in v.residents],"media":[{"name":m.stored_filename,"url":m.drive_url,"category":m.category} for m in v.media if not m.is_deleted]}

@app.post("/visits/{visit_id}/media")
async def upload_media(visit_id:str,request:Request,files:list[UploadFile]=File(...),db:Session=Depends(get_db)):
    user=current_user(request);visit=db.get(Visit,visit_id)
    if not visit:raise HTTPException(404,"Visit not found")
    patient=db.get(Patient,visit.patient_id);episode=db.get(Episode,visit.episode_id)
    token=getattr(request.state,"google_token",None) or request.session.get("google_token")
    if not token:raise HTTPException(401,"Google Drive authorization expired; sign in again")
    drive=DriveService(credentials_from_session(token),settings.google_drive_root_folder_id)
    try:
        if not patient.drive_folder_id:
            x=drive.create_folder(f"{patient.medical_record_number} - {patient.full_name}",settings.google_drive_root_folder_id);patient.drive_folder_id=x.id;patient.drive_folder_url=x.url
        if not episode.drive_folder_id:
            x=drive.create_folder(f"{episode.episode_number} - {episode.title}",patient.drive_folder_id);episode.drive_folder_id=x.id;episode.drive_folder_url=x.url
        if not visit.drive_folder_id:
            pod=f"POD {visit.pod_roman} ({visit.pod_number})" if visit.pod_number is not None else visit.visit_type
            x=drive.create_folder(f"{visit.visit_date.isoformat()} - {pod}",episode.drive_folder_id);visit.drive_folder_id=x.id;visit.drive_folder_url=x.url
            drive.upload_bytes("SOAP.txt",visit.original_soap.encode("utf-8"),"text/plain",visit.drive_folder_id)
        succeeded=[];failed=[];start=len(visit.media)
        for i,f in enumerate(files,start+1):
            try:
                if f.content_type not in {"image/jpeg","image/png","image/webp"}:raise ValueError("unsupported format")
                data=await f.read(); im=Image.open(io.BytesIO(data));width,height=im.size
                ext={"image/jpeg":"jpg","image/png":"png","image/webp":"webp"}[f.content_type];pod=f"POD{visit.pod_number:03d}" if visit.pod_number is not None else "VISIT"
                stored=f"{patient.medical_record_number_normalized}_{episode.episode_number}_{visit.visit_date.strftime('%Y%m%d')}_{pod}_{i:03d}.{ext}"
                item=drive.upload_bytes(stored,data,f.content_type,visit.drive_folder_id);m=Media(visit_id=visit.id,drive_file_id=item.id,drive_url=item.url,original_filename=safe_name(f.filename or "photo"),stored_filename=stored,mime_type=f.content_type,file_size=len(data),width=width,height=height,sequence_number=i,uploaded_by=user["email"]);db.add(m);db.flush();succeeded.append(stored)
            except Exception as exc:failed.append({"file":f.filename,"error":str(exc)})
        visit.save_state="complete" if not failed else "partial_failure";db.commit();return {"uploaded":succeeded,"failed":failed,"save_state":visit.save_state,"drive_url":visit.drive_folder_url}
    except Exception:
        visit.save_state="partial_failure";db.commit();raise

@app.get("/search")
def search(q:str,db:Session=Depends(get_db),user=Depends(current_user)):
    return [{"id":p.id,"name":p.full_name,"rm":p.medical_record_number,"hospital":p.primary_hospital} for p in search_cases(db,q)]

@app.get("/dashboard")
def dashboard(db:Session=Depends(get_db),user=Depends(current_user)):
    return {"patients":db.scalar(select(func.count()).select_from(Patient)),"episodes":db.scalar(select(func.count()).select_from(Episode)),"visits":db.scalar(select(func.count()).select_from(Visit).where(Visit.is_deleted.is_(False))),"photos":db.scalar(select(func.count()).select_from(Media).where(Media.is_deleted.is_(False)))}
