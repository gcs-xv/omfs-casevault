from __future__ import annotations
import os
import sys
import io
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime
from dataclasses import dataclass
from html import escape
from pathlib import Path
import httpx
import streamlit as st
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from streamlit_paste_button import paste_image_button

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

try:
    _embedded_default=st.secrets.get("EMBEDDED_MODE","false")
except FileNotFoundError:
    _embedded_default="false"
EMBEDDED_MODE=str(os.getenv("EMBEDDED_MODE",_embedded_default)).lower()=="true"
if EMBEDDED_MODE:
    from backend.services import drive_catalog
    if not hasattr(drive_catalog,"match_patient"):
        from importlib import reload
        drive_catalog=reload(drive_catalog)
    match_patient=drive_catalog.match_patient
    patient_from_folder=drive_catalog.patient_from_folder
    search_catalog=drive_catalog.search_catalog
    from backend.services.drive_service import DriveService
    from backend.services.soap_parser import parse_soap
    from backend.utils import normalization as normalization_utils
    if not hasattr(normalization_utils,"clinical_photo_name"):
        from importlib import reload
        normalization_utils=reload(normalization_utils)
    clinical_photo_name=normalization_utils.clinical_photo_name
    normalize_rm=normalization_utils.normalize_rm

API=os.getenv("API_URL","http://127.0.0.1:8000")
APP_URL="https://omfs-casevault-dj7trsufq6jykaeuddo7b4.streamlit.app/"
DRIVE_SCOPE="https://www.googleapis.com/auth/drive"

def setting(name,default=""):
    value=os.getenv(name)
    if value is not None:return value
    try:return st.secrets.get(name,default)
    except FileNotFoundError:return default

def archive_oauth_configured():
    return bool(setting("GOOGLE_CLIENT_ID") and setting("GOOGLE_CLIENT_SECRET"))

def archive_token_configured():
    return bool(setting("GOOGLE_ARCHIVE_REFRESH_TOKEN") or st.session_state.get("generated_archive_refresh_token"))

def password_auth_configured():
    return True

def drive_configured():
    return bool(setting("GOOGLE_DRIVE_ROOT_FOLDER_ID"))

def oauth_flow(state=None):
    redirect_uri=str(setting("CASEVAULT_PUBLIC_URL",APP_URL) if EMBEDDED_MODE else setting("GOOGLE_REDIRECT_URI",APP_URL))
    config={"web":{"client_id":setting("GOOGLE_CLIENT_ID"),"client_secret":setting("GOOGLE_CLIENT_SECRET"),"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[redirect_uri]}}
    return Flow.from_client_config(config,scopes=[DRIVE_SCOPE],state=state,redirect_uri=redirect_uri)

def state_signer():
    return URLSafeTimedSerializer(setting("SESSION_SECRET","casevault-change-me"),salt="casevault-archive-oauth")

def begin_archive_connect():
    user=st.session_state.get("app_user") or {}
    state=state_signer().dumps({"nonce":secrets.token_urlsafe(18),"username":user.get("username"),"role":user.get("role")})
    url,_=oauth_flow(state).authorization_url(access_type="offline",prompt="consent")
    st.link_button("CONNECT ARCHIVE GOOGLE ACCOUNT",url,type="primary",use_container_width=True)

def finish_archive_connect():
    code=st.query_params.get("code");state=st.query_params.get("state")
    if not code:return
    try:
        signed=state_signer().loads(state,max_age=600)
        flow=oauth_flow(state);flow.fetch_token(code=code)
        if not flow.credentials.refresh_token:raise ValueError("Google tidak mengembalikan refresh token. Cabut akses aplikasi di Google Account lalu coba lagi.")
        st.session_state.app_user={"username":signed.get("username") or "admin","role":signed.get("role") or "admin"}
        st.session_state.generated_archive_refresh_token=flow.credentials.refresh_token
        st.session_state.nav_page="⚙  Archive control"
        st.query_params.clear()
    except (BadSignature,SignatureExpired):st.error("Proses koneksi kedaluwarsa atau tidak valid. Silakan ulangi dari Archive control.")
    except Exception as exc:st.error(f"Koneksi akun arsip gagal: {exc}")

def archive_credentials():
    refresh_token=setting("GOOGLE_ARCHIVE_REFRESH_TOKEN") or st.session_state.get("generated_archive_refresh_token")
    if not refresh_token:raise ValueError("Archive account belum terhubung. Admin perlu menambahkan GOOGLE_ARCHIVE_REFRESH_TOKEN ke Streamlit Secrets.")
    credentials=st.session_state.get("archive_google_credentials")
    if not credentials or credentials.refresh_token!=refresh_token:
        credentials=Credentials(token=None,refresh_token=refresh_token,token_uri="https://oauth2.googleapis.com/token",client_id=setting("GOOGLE_CLIENT_ID"),client_secret=setting("GOOGLE_CLIENT_SECRET"),scopes=[DRIVE_SCOPE])
    if not credentials.valid:credentials.refresh(GoogleRequest())
    st.session_state.archive_google_credentials=credentials
    return credentials

def drive_service():
    return DriveService(archive_credentials(),setting("GOOGLE_DRIVE_ROOT_FOLDER_ID"))

def configured_users()->dict[str,dict]:
    return {
        "user":{"password":str(setting("CASEVAULT_USER_PASSWORD","user")),"role":"user"},
        "admin":{"password":str(setting("CASEVAULT_ADMIN_PASSWORD","admin")),"role":"admin"},
    }

def authenticate(username:str,password:str)->dict|None:
    record=configured_users().get(username.strip().lower())
    if not record or not hmac.compare_digest(password,record["password"]):return None
    return {"username":username.strip().lower(),"role":record["role"]}

def login_screen():
    left,center,right=st.columns([1,1.25,1])
    with center:
        st.markdown('<div style="height:8vh"></div><div class="cv-brand" style="justify-content:center"><div class="cv-logo" style="color:#102330;border-color:#68818a">OM</div><div><strong style="color:#102330">CaseVault</strong><span style="color:#6e7678">OMFS Surgical Case Atlas</span></div></div>',unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("## Welcome back")
            st.caption("Sign in to open the private surgical archive.")
            with st.form("casevault_login"):
                username=st.text_input("Username",autocomplete="username")
                password=st.text_input("Password",type="password",autocomplete="current-password")
                submitted=st.form_submit_button("SIGN IN  →",type="primary",use_container_width=True)
            if submitted:
                locked_until=st.session_state.get("login_locked_until",0)
                if time.time()<locked_until:st.error("Terlalu banyak percobaan. Tunggu sebentar lalu coba lagi.")
                else:
                    user=authenticate(username,password)
                    if user:
                        st.session_state.app_user=user;st.session_state.login_failures=0;st.rerun()
                    failures=st.session_state.get("login_failures",0)+1;st.session_state.login_failures=failures
                    if failures>=5:st.session_state.login_locked_until=time.time()+30
                    st.error("Username atau password salah.")
            st.markdown('<div class="cv-meta" style="text-align:center;margin-top:.8rem">Private archive · Password protected · No Google sign-in</div>',unsafe_allow_html=True)

@st.cache_data(ttl=60,show_spinner=False)
def list_drive_folders(_drive,parent_id:str)->list[dict]:
    """Hot-reload-safe folder listing; works even if Streamlit cached an older service module."""
    drive=_drive
    if hasattr(drive,"list_folders"):return drive.list_folders(parent_id)
    folder_mime="application/vnd.google-apps.folder"
    q=f"'{parent_id}' in parents and mimeType = '{folder_mime}' and trashed = false"
    result=drive.api.files().list(q=q,pageSize=1000,fields="files(id,name,webViewLink,createdTime,modifiedTime)",orderBy="name_natural",supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
    return result.get("files",[])

@st.cache_data(ttl=90,show_spinner=False)
def list_drive_files(_drive,parent_id:str)->list[dict]:
    drive=_drive
    if hasattr(drive,"list_files"):return drive.list_files(parent_id)
    folder_mime="application/vnd.google-apps.folder"
    q=f"'{parent_id}' in parents and mimeType != '{folder_mime}' and trashed = false"
    result=drive.api.files().list(q=q,pageSize=1000,fields="files(id,name,mimeType,size,webViewLink,createdTime,modifiedTime)",orderBy="name_natural",supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
    return result.get("files",[])

@st.cache_data(ttl=600,max_entries=256,show_spinner=False)
def download_drive_bytes(_drive,file_id:str)->bytes:
    drive=_drive
    if hasattr(drive,"download_bytes"):return drive.download_bytes(file_id)
    return drive.api.files().get_media(fileId=file_id,supportsAllDrives=True).execute()

@st.cache_data(ttl=90,show_spinner=False)
def list_drive_visit_metadata(_drive,root_id:str)->list[dict]:
    """Read metadata even when Streamlit still holds an older DriveService class."""
    drive=_drive
    if hasattr(drive,"list_visit_metadata"):return drive.list_visit_metadata()
    q="name = 'casevault-metadata.json' and trashed = false"
    result=drive.api.files().list(q=q,pageSize=1000,fields="files(id,parents)",supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
    rows=[]
    for item in result.get("files",[]):
        try:
            payload=json.loads(drive.api.files().get_media(fileId=item["id"],supportsAllDrives=True).execute().decode("utf-8"))
            if payload.get("casevault_root_id")==root_id:
                payload["metadata_file_id"]=item["id"];rows.append(payload)
        except Exception:
            continue
    return rows

def clear_drive_cache():
    list_drive_folders.clear();list_drive_files.clear();download_drive_bytes.clear();list_drive_visit_metadata.clear()

def drive_patients():
    drive=drive_service()
    return [patient_from_folder(x) for x in list_drive_folders(drive,drive.root_id)]

def prepare_drive_defaults(data:dict)->dict:
    """Choose fast filing defaults while keeping them editable."""
    episode=data.setdefault("episode",{});visit=data["visit"]
    episode.setdefault("title",(data.get("diagnoses") or data.get("procedures") or ["Clinical Episode"])[0])
    existing=[];episode_names={};patient_found=False
    try:
        rm=normalize_rm(data["patient"].get("medical_record_number"));drive=drive_service()
        match=next((p for p in drive_patients() if p["rm_normalized"]==rm),None)
        if match:
            patient_found=True
            for folder in list_drive_folders(drive,match["id"]):
                number=re.match(r"(?i)^EP\s*0*(\d+)",folder["name"])
                if number:
                    n=int(number.group(1));existing.append(n);episode_names[n]=folder["name"]
    except Exception:
        pass
    existing=sorted(set(existing));phase=visit.get("visit_phase") or "Terjaring"
    suggested=(max(existing)+1 if phase=="Terjaring" and existing else max(existing) if existing else 1)
    episode.setdefault("number",suggested);data["_existing_episode_numbers"]=existing;data["_episode_folder_names"]=episode_names;data["_drive_patient_found"]=patient_found
    if phase=="POD" and visit.get("pod_number") is not None:visit["pod_roman"]=int_to_roman(int(visit["pod_number"]))
    return data

def int_to_roman(number:int)->str:
    pairs=((1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I"));out=[]
    for value,symbol in pairs:
        while number>=value:out.append(symbol);number-=value
    return "".join(out)

@dataclass
class MemoryUpload:
    name:str
    data:bytes
    type:str="image/png"
    def getvalue(self):return self.data

def pasted_photos()->list[MemoryUpload]:
    stored=st.session_state.setdefault("pasted_photos",{})
    result=paste_image_button("PASTE IMAGE FROM CLIPBOARD",text_color="#ffffff",background_color="#087f78",hover_background_color="#086e68",key="clipboard_photo")
    if result.image_data is not None:
        buffer=io.BytesIO();result.image_data.save(buffer,format="PNG");data=buffer.getvalue();digest=hashlib.sha256(data).hexdigest()
        if digest not in stored:stored[digest]=MemoryUpload(f"clipboard_{len(stored)+1:02d}.png",data)
    return list(stored.values())

def patient_folder_name(patient:dict,care_setting:str|None=None)->str:
    identity=" ".join(x for x in [patient.get("title"),patient.get("full_name")] if x).strip()
    age=f"{patient.get('age')} {patient.get('age_unit') or 'Tahun'}" if patient.get("age") else None
    parts=[identity,patient.get("sex"),age,care_setting or patient.get("care_setting"),patient.get("hospital"),patient.get("insurance"),f"RM {patient.get('medical_record_number')}"]
    return " / ".join(str(x) for x in parts if x)

def save_visit_to_drive(data:dict,photos:list)->dict:
    drive=drive_service();patient=data["patient"];visit=data["visit"];episode=data["episode"]
    rm=normalize_rm(patient.get("medical_record_number"))
    if not rm:raise ValueError("Nomor RM wajib diisi.")
    if not visit.get("visit_date"):raise ValueError("Tanggal kunjungan wajib diisi.")
    identity_match=match_patient(drive_patients(),patient)
    if identity_match["status"]=="conflict":raise ValueError(identity_match["reason"])
    matched_patient=identity_match.get("patient")
    if matched_patient:
        patient_folder_id=matched_patient["id"];patient_drive_url=matched_patient["drive_url"]
    else:
        created=drive.create_folder(patient_folder_name(patient,visit.get("location")),drive.root_id);patient_folder_id=created.id;patient_drive_url=created.url
    episode_number=int(episode["number"]);prefix=f"EP{episode_number:02d}"
    episode_folders=list_drive_folders(drive,patient_folder_id)
    existing=next((x for x in episode_folders if x["name"].upper().startswith(prefix)),None)
    if existing:
        episode_folder_id=existing["id"]
    else:
        episode_folder_id=drive.create_folder(f"{prefix} - {episode['title'] or 'Clinical Episode'}",patient_folder_id).id
    visit_sequence=len(list_drive_folders(drive,episode_folder_id))+1
    phase=visit["visit_phase"]
    label=phase
    if phase=="POD":
        pod=int(visit.get("pod_number") or 0)
        if pod<0:raise ValueError("Nomor POD tidak valid.")
        roman=visit.get("pod_roman") or int_to_roman(pod)
        label=f"POD {roman} ({pod})" if roman else "POD 0"
    visit_folder=drive.create_folder(f"{visit['visit_date']} - {label}",episode_folder_id)
    drive.upload_bytes("SOAP.txt",data["soap"]["original_soap"].encode("utf-8"),"text/plain",visit_folder.id)
    roles={"dpjp":(data.get("dpjp") or {}).get("full_name"),"operator":data.get("operator"),"assistant_operators":data.get("assistant_operators",[])}
    search_blob="\n".join(str(x) for x in [patient.get("full_name"),patient.get("medical_record_number"),*data.get("diagnoses",[]),*data.get("procedures",[]),roles["dpjp"],roles["operator"],*roles["assistant_operators"],data["soap"].get("assessment"),data["soap"].get("plan")] if x)
    uploaded=[];failed=[];attachments=[]
    for i,file in enumerate(photos,1):
        try:
            name=clinical_photo_name(patient.get("full_name") or "Patient",patient.get("medical_record_number") or "",episode_number,visit_sequence,visit["visit_date"],phase,visit.get("pod_roman"),i,file.name)
            drive.upload_bytes(name,file.getvalue(),file.type,visit_folder.id);uploaded.append(name);attachments.append({"original_name":file.name,"archive_name":name,"mime_type":file.type})
        except Exception as exc:failed.append({"file":file.name,"error":str(exc)})
    metadata={"schema_version":2,"casevault_root_id":drive.root_id,"patient":patient,"patient_folder_id":patient_folder_id,"patient_drive_url":patient_drive_url,"episode":episode,"episode_folder_id":episode_folder_id,"visit":{**visit,"visit_sequence":visit_sequence},"visit_folder_id":visit_folder.id,"visit_drive_url":visit_folder.url,"diagnoses":data.get("diagnoses",[]),"procedures":data.get("procedures",[]),"roles":roles,"soap":data.get("soap",{}),"attachments":attachments,"search_blob":search_blob,"saved_at":datetime.utcnow().isoformat()+"Z","saved_by":st.session_state.app_user["username"],"saved_by_role":st.session_state.app_user["role"]}
    drive.upload_bytes("casevault-metadata.json",json.dumps(metadata,ensure_ascii=False,indent=2).encode("utf-8"),"application/json",visit_folder.id,{"casevault_root":drive.root_id,"casevault_type":"visit_metadata"})
    clear_drive_cache()
    return {"uploaded":uploaded,"failed":failed,"drive_url":visit_folder.url,"patient_drive_url":patient_drive_url,"visit_sequence":visit_sequence,"patient_match":identity_match.get("match_type","new"),"save_state":"complete" if not failed else "partial_failure"}

st.set_page_config(page_title="OMFS CaseVault · Surgical Case Atlas",page_icon="🦷",layout="wide",initial_sidebar_state="auto")
finish_archive_connect()
if st.query_params.get("session_token"):
    st.session_state.api_session_token=st.query_params["session_token"]
    st.query_params.clear()
st.markdown("""<style>
:root{--atlas-ink:#102330;--atlas-navy:#0b1d29;--atlas-blue:#2d7892;--atlas-xray:#9ec9d4;--atlas-coral:#c85f4d;--atlas-bone:#f2eee5;--atlas-paper:#fffdf8;--atlas-rule:#c9c3b6;--atlas-muted:#6e7678;--atlas-brass:#a78555}
html,body,[class*="css"]{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.stApp{color:var(--atlas-ink);background-color:var(--atlas-bone);background-image:linear-gradient(rgba(16,35,48,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(16,35,48,.025) 1px,transparent 1px);background-size:28px 28px}
.block-container{max-width:1280px;padding:2.1rem 2.4rem 5rem}.main .block-container{animation:atlas-enter .34s cubic-bezier(.2,.75,.3,1)}@keyframes atlas-enter{from{opacity:.25;transform:translateY(7px)}to{opacity:1;transform:none}}
[data-testid="stSidebar"]{background:var(--atlas-navy);border-right:1px solid #24404f;box-shadow:10px 0 30px #06141d18}[data-testid="stSidebar"]:before{content:"";position:absolute;inset:0 0 auto;height:4px;background:var(--atlas-coral)}
[data-testid="stSidebar"] *{color:#e9e6dc}[data-testid="stSidebar"] [role="radiogroup"]{gap:.15rem;border-top:1px solid #29404c;padding-top:.9rem}
[data-testid="stSidebar"] label[data-baseweb="radio"]>div:first-child{display:none}
[data-testid="stSidebar"] label[data-baseweb="radio"]{padding:.76rem .6rem;border-radius:1px;border-left:2px solid transparent;transition:.18s;background:transparent;font-size:.88rem;letter-spacing:.015em}
[data-testid="stSidebar"] label[data-baseweb="radio"]:hover{background:#ffffff08;border-left-color:#789aa8}[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked){background:#e6eef10d;border-left-color:var(--atlas-coral)}
.cv-brand{display:flex;gap:.8rem;align-items:center;padding:.35rem 0 1.4rem}.cv-logo{width:46px;height:46px;border:1px solid #6f8994;display:grid;place-items:center;font-family:Georgia,"Times New Roman",serif;font-weight:700;font-size:.92rem;letter-spacing:.04em;color:#f4eee1;position:relative}.cv-logo:before,.cv-logo:after{content:"";position:absolute;background:var(--atlas-coral)}.cv-logo:before{width:13px;height:1px;bottom:7px;right:5px}.cv-logo:after{width:1px;height:13px;bottom:1px;right:11px}.cv-brand strong{display:block;font-family:Georgia,"Times New Roman",serif;font-size:1.06rem;letter-spacing:.015em}.cv-brand span{font-size:.6rem;color:#9fb0b6;letter-spacing:.19em;text-transform:uppercase}
.cv-hero{position:relative;overflow:hidden;min-height:194px;padding:2.15rem 17rem 1.8rem 0;border-top:3px double var(--atlas-ink);border-bottom:1px solid var(--atlas-rule);margin-bottom:1.5rem}.cv-kicker{display:flex;align-items:center;gap:.6rem;color:var(--atlas-coral);font-size:.64rem;font-weight:800;letter-spacing:.19em;text-transform:uppercase;margin-bottom:.8rem}.cv-kicker:before{content:"";width:28px;height:1px;background:var(--atlas-coral)}.cv-hero h1{font-family:Georgia,"Times New Roman",serif;font-size:clamp(2.15rem,4.5vw,3.65rem);font-weight:500;line-height:1.02;letter-spacing:-.045em;margin:0;color:var(--atlas-ink);text-wrap:balance}.cv-hero p{color:#5d686b;font-size:.94rem;line-height:1.6;margin:.75rem 0 0;max-width:690px}.cv-folio{position:absolute;right:0;top:.9rem;color:#70797a;font-size:.58rem;letter-spacing:.18em;text-transform:uppercase;writing-mode:vertical-rl}.cv-anatomy{position:absolute;right:1.9rem;bottom:-1.15rem;width:210px;color:var(--atlas-blue);opacity:.24}.cv-anatomy svg{width:100%;height:auto}.cv-anatomy path,.cv-anatomy circle{vector-effect:non-scaling-stroke}
.cv-steps{position:relative;display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin:.2rem 0 1.35rem;border:1px solid var(--atlas-rule);background:var(--atlas-paper)}.cv-step{position:relative;display:flex;align-items:center;gap:.75rem;padding:.85rem 1rem;color:#768084;border-right:1px solid var(--atlas-rule);font-size:.78rem;letter-spacing:.04em;text-transform:uppercase}.cv-step:last-child{border-right:0}.cv-step b{font-family:Georgia,"Times New Roman",serif;font-size:1.2rem;font-weight:500;color:#a1a29d}.cv-step.active{color:var(--atlas-ink);background:#edf3f3}.cv-step.active:after{content:"";position:absolute;inset:auto 0 -1px;height:3px;background:var(--atlas-coral)}.cv-step.active b{color:var(--atlas-coral)}.cv-step.done{background:#f7f4ed;color:#59686b}.cv-step.done b{color:var(--atlas-blue)}
.cv-note{position:relative;overflow:hidden;background:var(--atlas-navy);border-left:5px solid var(--atlas-coral);padding:1.55rem 1.45rem;color:#eae7df;min-height:220px;box-shadow:9px 12px 0 #d8d1c3}.cv-note:after{content:"01";position:absolute;right:-.15rem;bottom:-2.2rem;font-family:Georgia,"Times New Roman",serif;font-size:8rem;color:#fff;opacity:.035}.cv-note .num{font-size:.6rem;letter-spacing:.2em;text-transform:uppercase;color:#91bac7}.cv-note h3{font-family:Georgia,"Times New Roman",serif;font-weight:500;font-size:1.5rem;color:#fff;margin:.65rem 0}.cv-note p{color:#b9c6c8;font-size:.88rem;line-height:1.5}.cv-note ul{padding-left:1.05rem;color:#d9dcd7;font-size:.8rem;line-height:1.7}
.cv-avatar{width:46px;height:46px;border:1px solid #9fb4b8;background:#e5eded;color:var(--atlas-blue);display:grid;place-items:center;font-family:Georgia,"Times New Roman",serif;font-weight:700;letter-spacing:.04em}.cv-meta{color:var(--atlas-muted);font-size:.78rem;line-height:1.45;margin-top:.22rem}.cv-pill{display:inline-block;padding:.21rem .48rem;border:1px solid #b8c7c7;background:transparent;color:#47666e;font-size:.59rem;font-weight:750;letter-spacing:.075em;text-transform:uppercase;margin:.18rem .25rem .08rem 0}.cv-empty{text-align:center;padding:3.4rem 1rem;background:var(--atlas-paper);border:1px dashed #aaa89f;color:var(--atlas-muted)}.cv-empty .icon{font-family:Georgia,"Times New Roman",serif;font-size:2.4rem;color:var(--atlas-blue);margin-bottom:.6rem}.cv-section-title{font-family:Georgia,"Times New Roman",serif;font-size:1.22rem;color:var(--atlas-ink);margin:.15rem 0 .2rem}.cv-section-sub{color:var(--atlas-muted);font-size:.78rem;line-height:1.5;margin-bottom:.9rem}
.cv-record-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem;margin:.15rem 0 .75rem}.cv-record-fact{min-height:66px;padding:.62rem .7rem;background:#f7f4ed;border:1px solid #d7d1c5}.cv-record-fact b{display:block;color:#698087;font-size:.55rem;letter-spacing:.13em;text-transform:uppercase;margin-bottom:.3rem}.cv-record-fact span{display:block;color:#263b44;font-size:.78rem;line-height:1.35;max-height:3.25em;overflow:auto}.cv-soap-scroll{height:min(46vh,430px);min-height:260px;overflow:auto;padding:1rem 1.05rem;background:#f8f6f0;border:1px solid #d4cec2;color:#263b44;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;line-height:1.55;white-space:pre-wrap}.cv-viewer-note{font-size:.7rem;color:var(--atlas-muted);margin:.25rem 0 .7rem}.cv-photo-name{font-family:Georgia,"Times New Roman",serif;font-size:.82rem;color:var(--atlas-ink);overflow-wrap:anywhere;margin:.25rem 0}[data-testid="stImage"] img{max-height:34vh;object-fit:contain;background:#eef1ef}.cv-drive-link{width:32px;height:32px;display:inline-grid;place-items:center;border:1px solid #c7c3b9;background:#fffdf8;transition:.16s;cursor:pointer}.cv-drive-link:hover{transform:translateY(-1px);border-color:var(--atlas-blue);background:#edf3f3}.cv-drive-link svg{width:18px;height:18px;display:block}
.cv-profile{display:flex;align-items:center;gap:1rem;padding:.35rem 0 .75rem}.cv-profile .cv-avatar{width:62px;height:62px;font-size:1.16rem}.cv-profile h2{font-family:Georgia,"Times New Roman",serif;font-weight:500;margin:0;font-size:1.65rem}.cv-episode-head{display:flex;align-items:center;gap:1rem;padding:.2rem 0 .7rem}.cv-case-no{min-width:56px;font-family:Georgia,"Times New Roman",serif;font-size:2.35rem;line-height:1;color:var(--atlas-coral);border-right:1px solid var(--atlas-rule);padding-right:.8rem}.cv-episode-title{font-family:Georgia,"Times New Roman",serif;font-size:1.22rem;font-weight:500;color:var(--atlas-ink)}.cv-episode-meta{font-size:.65rem;letter-spacing:.06em;text-transform:uppercase;color:var(--atlas-muted);margin-top:.28rem}.cv-visit{position:relative;border-left:1px solid var(--atlas-blue);padding:.15rem 0 .15rem 1.25rem;margin:.65rem 0 1.1rem}.cv-visit:before{content:"";position:absolute;left:-5px;top:.45rem;width:9px;height:9px;border:2px solid var(--atlas-paper);background:var(--atlas-coral);box-shadow:0 0 0 1px var(--atlas-coral)}.cv-visit-title{font-family:Georgia,"Times New Roman",serif;font-size:1.02rem;color:var(--atlas-ink)}.cv-field{margin:.58rem 0}.cv-field b{display:block;color:#698087;font-size:.57rem;letter-spacing:.14em;text-transform:uppercase;margin-bottom:.18rem}.cv-field span{color:#263b44;font-size:.82rem;line-height:1.48}.cv-card-folio{color:var(--atlas-coral);font-family:Georgia,"Times New Roman",serif;font-size:.66rem;letter-spacing:.13em;text-transform:uppercase;border-bottom:1px solid #d8d2c6;padding-bottom:.45rem;margin-bottom:.75rem}
[data-testid="stVerticalBlockBorderWrapper"]{background:rgba(255,253,248,.94);border-color:#cdc7ba!important;border-radius:2px!important;box-shadow:2px 3px 0 #d9d2c5;transition:transform .18s ease,border-color .18s ease}[data-testid="stVerticalBlockBorderWrapper"]:hover{border-color:#9d9a92!important}
[data-baseweb="input"]>div,[data-baseweb="textarea"]>div,[data-baseweb="select"]>div{border-color:#bdb9ae!important;border-radius:2px!important;background:#fffefb!important;box-shadow:none!important}input,textarea{color:var(--atlas-ink)!important}input:focus,textarea:focus{caret-color:var(--atlas-coral)}
[data-baseweb="tab-list"]{gap:0;border-bottom:1px solid var(--atlas-rule)}[data-baseweb="tab"]{border-radius:0;padding:.72rem 1rem;font-size:.75rem;letter-spacing:.04em}[aria-selected="true"][data-baseweb="tab"]{color:var(--atlas-ink)!important;background:#e8efef;border-top:2px solid var(--atlas-blue)}
div.stButton>button,div.stLinkButton>a{border-radius:2px;min-height:2.7rem;font-size:.68rem;font-weight:780;letter-spacing:.08em;text-transform:uppercase;transition:.18s;border-color:#9f9b91;background:#fffdf8;color:var(--atlas-ink)}div.stButton>button:hover,div.stLinkButton>a:hover{transform:translateY(-1px);border-color:var(--atlas-coral);color:var(--atlas-coral)}div.stButton>button[kind="primary"]{background:var(--atlas-navy);border:1px solid var(--atlas-navy);box-shadow:4px 4px 0 var(--atlas-coral);color:#fff}div.stButton>button[kind="primary"]:hover{background:#163441;color:#fff;box-shadow:2px 2px 0 var(--atlas-coral)}
[data-testid="stFileUploaderDropzone"]{background:#f8f5ee;border:1px dashed #9aa9aa;border-radius:2px}[data-testid="stMetric"]{background:var(--atlas-paper);border-top:2px solid var(--atlas-blue);padding:1rem;border-bottom:1px solid var(--atlas-rule)}[data-testid="stExpander"]{border-color:var(--atlas-rule)!important;border-radius:2px!important}hr{border-color:var(--atlas-rule)}
@media(max-width:900px){.block-container{padding:1.45rem 1.25rem 4rem}.cv-hero{padding-right:10rem}.cv-anatomy{right:-1.5rem;width:180px;opacity:.15}.cv-folio{display:none}.cv-note{min-height:auto;margin-bottom:1.2rem}}
@media(max-width:640px){.block-container{padding:1.05rem .82rem 3.5rem}.cv-hero{min-height:auto;padding:1.4rem 0 1.25rem}.cv-hero h1{font-size:2.15rem;max-width:92%}.cv-hero p{font-size:.84rem;line-height:1.5}.cv-anatomy{right:-4.2rem;bottom:-.8rem;width:155px;opacity:.08}.cv-steps{grid-template-columns:1fr}.cv-step{border-right:0;border-bottom:1px solid var(--atlas-rule);padding:.58rem .72rem}.cv-step:last-child{border-bottom:0}.cv-note{box-shadow:5px 6px 0 #d8d1c3}.cv-profile{align-items:flex-start}.cv-profile .cv-avatar{width:50px;height:50px;flex:0 0 50px}.cv-profile h2{font-size:1.3rem}.cv-episode-head{align-items:flex-start}.cv-case-no{min-width:42px;font-size:1.75rem;padding-right:.55rem}.cv-episode-title{font-size:1rem}.cv-pill{font-size:.54rem}.cv-visit{padding-left:1rem}.cv-field span{font-size:.78rem}.cv-record-grid{grid-template-columns:1fr 1fr;gap:.4rem}.cv-record-fact{min-height:60px;padding:.5rem}.cv-soap-scroll{height:42vh;min-height:220px;padding:.75rem;font-size:.71rem}div.stButton>button,div.stLinkButton>a{min-height:2.6rem}.cv-brand{padding-bottom:1rem}}
@media(prefers-reduced-motion:reduce){.main .block-container{animation:none}*{scroll-behavior:auto!important}}
</style>""",unsafe_allow_html=True)

def page_header(kicker:str,title:str,subtitle:str):
    jaw='<svg viewBox="0 0 240 190" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M47 28C29 52 25 82 36 113c10 29 31 46 56 52 15 4 25 2 28 1 3 1 13 3 28-1 25-6 46-23 56-52 11-31 7-61-11-85" stroke="currentColor" stroke-width="1.5"/><path d="M48 31c8 9 14 27 16 44 2 24 8 44 21 57 12 12 24 14 35 14s23-2 35-14c13-13 19-33 21-57 2-17 8-35 16-44M69 83c13-8 29-12 51-12s38 4 51 12M83 127c9-8 21-12 37-12s28 4 37 12" stroke="currentColor" stroke-width="1"/><path d="M86 75v37m17-40v43m17-44v44m17-43v43m17-40v37" stroke="currentColor" stroke-width=".75"/><circle cx="120" cy="146" r="3" stroke="currentColor"/></svg>'
    st.markdown(f'<div class="cv-hero"><div class="cv-kicker">{kicker}</div><h1>{title}</h1><p>{subtitle}</p><div class="cv-folio">OMFS · CASE ATLAS · 2026</div><div class="cv-anatomy" aria-hidden="true">{jaw}</div></div>',unsafe_allow_html=True)

def workflow_steps(active:int):
    labels=("Source note","Clinical indexing","Drive archive")
    html="".join(f'<div class="cv-step {"done" if i<active else "active" if i==active else ""}"><b>{"✓" if i<active else f"{i+1:02d}"}</b><span>{label}</span></div>' for i,label in enumerate(labels))
    st.markdown(f'<div class="cv-steps">{html}</div>',unsafe_allow_html=True)

def initials(name:str)->str:
    words=[x for x in name.replace(".","").split() if x]
    return "".join(x[0].upper() for x in words[-2:]) if words else "CV"

def compact_values(values)->list[str]:
    """Keep optional clinical fields concise and omit empty/duplicate values."""
    seen=set();result=[]
    for value in values or []:
        if value is None:continue
        value=str(value).strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold());result.append(value)
    return result

def patient_metadata(rows:list[dict],patient:dict)->list[dict]:
    patient_id=patient.get("id");rm=normalize_rm(patient.get("rm"))
    matches=[]
    for row in rows:
        row_rm=normalize_rm((row.get("patient") or {}).get("medical_record_number"))
        if row.get("patient_folder_id")==patient_id or (rm and row_rm==rm):matches.append(row)
    return sorted(matches,key=lambda x:(x.get("visit",{}).get("visit_date") or "",x.get("saved_at") or ""),reverse=True)

def episode_sort_key(folder:dict):
    number=re.match(r"(?i)^EP\s*0*(\d+)",folder.get("name", ""))
    return (int(number.group(1)) if number else 999999,folder.get("name","").casefold())

def show_optional_field(label:str,values):
    if isinstance(values,str):values=[values]
    values=compact_values(values)
    if values:st.markdown(f'<div class="cv-field"><b>{escape(label)}</b><span>{escape(" · ".join(values))}</span></div>',unsafe_allow_html=True)

def drive_icon_link(url:str|None,label:str="Open in Google Drive"):
    if not url:return
    icon='<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0F9D58" d="M8.2 2h5.1l7.8 13.5h-5.2z"/><path fill="#4285F4" d="M5.5 16h15.6l-2.6 4.5H2.9z"/><path fill="#F4B400" d="M8.2 2l2.6 4.5-7.9 14H2.8L.3 16z"/></svg>'
    st.markdown(f'<a class="cv-drive-link" href="{escape(url,quote=True)}" target="_blank" rel="noopener noreferrer" title="{escape(label,quote=True)}" aria-label="{escape(label,quote=True)}">{icon}</a>',unsafe_allow_html=True)

def render_visit_contents(drive,visit_folder:dict,row:dict|None,key_prefix:str):
    row=row or {};roles=row.get("roles",{})
    try:
        files=list_drive_files(drive,visit_folder["id"])
        soap=(row.get("soap") or {}).get("original_soap") or ""
        soap_file=next((item for item in files if item.get("name","").casefold()=="soap.txt"),None)
        if not soap and soap_file:soap=download_drive_bytes(drive,soap_file["id"]).decode("utf-8",errors="replace")
        photos=[item for item in files if str(item.get("mimeType","")).startswith("image/")]
    except Exception as exc:
        st.warning(f"Record files could not be loaded: {exc}");return
    soap_tab,photos_tab=st.tabs(["SOAP REPORT",f"PHOTOS · {len(photos)}"])
    with soap_tab:
        facts=[("Procedure",row.get("procedures",[])),("Diagnosis",row.get("diagnoses",[])),("DPJP",roles.get("dpjp")),("Operator",roles.get("operator")),("Assistant operator",roles.get("assistant_operators",[])),("Archive folder",visit_folder.get("name"))]
        cards=[]
        for label,value in facts:
            values=compact_values(value if isinstance(value,list) else [value])
            if values:cards.append(f'<div class="cv-record-fact"><b>{escape(label)}</b><span>{escape(" · ".join(values))}</span></div>')
        if cards:st.markdown(f'<div class="cv-record-grid">{"".join(cards)}</div>',unsafe_allow_html=True)
        if soap:
            st.markdown(f'<div class="cv-soap-scroll">{escape(soap)}</div>',unsafe_allow_html=True)
            st.download_button("DOWNLOAD SOAP",soap,file_name="SOAP.txt",mime="text/plain",key=f"soap_download_{key_prefix}")
        else:st.caption("SOAP.txt was not found in this legacy visit folder.")
    with photos_tab:
        if not photos:
            st.caption("No image files found in this visit folder.")
        else:
            show=st.toggle("LOAD ALL PHOTOS",key=f"show_photos_{key_prefix}",help="All clinical photos are downloaded privately from Drive only when enabled.")
            if show:
                grid=st.columns(3,gap="small")
                for index,item in enumerate(photos):
                    with grid[index%3]:
                        try:
                            data=download_drive_bytes(drive,item["id"])
                            st.image(data,use_container_width=True)
                            st.markdown(f'<div class="cv-photo-name">{escape(item.get("name") or "Clinical photo")}</div>',unsafe_allow_html=True)
                            st.download_button("DOWNLOAD",data,file_name=item.get("name") or f"clinical-photo-{index+1}.jpg",mime=item.get("mimeType") or "application/octet-stream",key=f"photo_download_{key_prefix}_{item['id']}",use_container_width=True)
                            drive_icon_link(item.get("webViewLink"),"Open photo in Google Drive")
                        except Exception as exc:st.warning(f"{item.get('name','Photo')}: {exc}")

def api(method,path,**kwargs):
    try:
        if EMBEDDED_MODE:
            return embedded_api(method,path,**kwargs)
        if st.session_state.get("api_session_token"):
            kwargs.setdefault("headers",{})["Authorization"]=f"Bearer {st.session_state.api_session_token}"
        r=httpx.request(method,API+path,timeout=90,**kwargs)
        if r.status_code==401:
            st.error("Sign in is required. Configure Google OAuth, or use AUTH_DISABLED=true only for a private local development session.");return None
        r.raise_for_status();return r.json()
    except Exception as e:st.error(f"CaseVault service unavailable: {e}");return None

def embedded_api(method,path,**kwargs):
    """Single-process demo adapter for Streamlit Community Cloud."""
    if path=="/health":return {"status":"ok","auth_configured":password_auth_configured(),"drive_configured":drive_configured() and archive_token_configured(),"signed_in":bool(st.session_state.get("app_user")),"mode":"Streamlit Cloud"}
    if path=="/parser/soap":return parse_soap(kwargs["json"]["soap"])
    raise ValueError(f"Unsupported embedded route: {method} {path}")

def sidebar():
    st.sidebar.markdown('<div class="cv-brand"><div class="cv-logo">OM</div><div><strong>CaseVault</strong><span>OMFS Surgical Atlas</span></div></div>',unsafe_allow_html=True)
    page=st.sidebar.radio("Navigation",["＋  Case intake","◫  Case registry","⌕  Atlas index","⚙  Archive control"],label_visibility="collapsed",key="nav_page")
    st.sidebar.markdown("---")
    health=api("GET","/health")
    if health:
        st.sidebar.caption("●  CASEVAULT ONLINE")
        if EMBEDDED_MODE and st.session_state.get("app_user"):
            user=st.session_state.app_user
            st.sidebar.markdown(f"<small>Signed in as</small><br><b>{escape(user['username'])}</b> <span class='cv-pill'>{escape(user['role'])}</span>",unsafe_allow_html=True)
    return page

def preview(d):
    p=d["patient"];v=d["visit"]
    episode=d.setdefault("episode",{});existing=d.get("_existing_episode_numbers",[]);episode_names=d.get("_episode_folder_names",{})
    st.markdown('<div class="cv-section-title">Quick check</div><div class="cv-section-sub">CaseVault filled the details automatically. Change only what is wrong.</div>',unsafe_allow_html=True)
    identity_status="Existing Drive patient" if d.get("_drive_patient_found") else "New patient folder"
    st.markdown(f'<div style="display:flex;align-items:center;gap:.8rem;margin:.2rem 0 1rem"><div class="cv-avatar">{initials(p.get("full_name") or "CV")}</div><div><strong>{escape(p.get("full_name") or "Patient not detected")}</strong><div class="cv-meta">RM {escape(p.get("medical_record_number") or "not detected")} · {escape(identity_status)} · {escape(v.get("visit_date") or "date missing")}</div></div></div>',unsafe_allow_html=True)
    episode_choices=sorted(set(existing+[max(existing)+1 if existing else 1,int(episode.get("number") or 1)]))
    def episode_label(number):
        if number not in existing:return f"Episode {number} — New episode"
        saved_name=episode_names.get(number,f"Episode {number}")
        title=re.sub(r"(?i)^EP\s*0*\d+\s*[-–—:]?\s*","",saved_name).strip()
        return f"Episode {number} — {title or 'Existing episode'}"
    c1,c2=st.columns(2)
    episode["number"]=c1.selectbox("File into",episode_choices,index=episode_choices.index(int(episode.get("number") or episode_choices[0])),format_func=episode_label)
    phases=["Terjaring","Pre-op","Intra-op","POD"];suggested=v.get("visit_phase") if v.get("visit_phase") in phases else "Terjaring"
    v["visit_phase"]=c2.selectbox("Visit stage",phases,index=phases.index(suggested))
    if episode["number"] not in existing:
        suggested_title=episode.get("title") or (d.get("procedures") or d.get("diagnoses") or ["Clinical Episode"])[0]
        episode["title"]=st.text_input("Name this new episode",suggested_title,placeholder="e.g. Insisi biopsi or Marginal resection",help="This becomes the Drive folder name, for example: EP01 - Insisi biopsi")
    else:
        saved_name=episode_names.get(episode["number"],f"EP{episode['number']:02d}")
        episode["title"]=re.sub(r"(?i)^EP\s*0*\d+\s*[-–—:]?\s*","",saved_name).strip() or "Clinical Episode"
        st.caption(f"Using existing Drive folder: {saved_name}")
    if v["visit_phase"]=="POD":
        v["pod_number"]=st.number_input("Post-operative day",0,1000,int(v.get("pod_number") or 0),help="Roman numeral is generated automatically.")
        v["pod_roman"]=int_to_roman(int(v["pod_number"]))
        st.caption(f"Automatically filed as POD {v['pod_roman'] or '0'} — no Roman numeral entry needed.")
    else:v["pod_number"]=None;v["pod_roman"]=None
    phase_label=f"POD {v.get('pod_roman') or '0'}" if v["visit_phase"]=="POD" else v["visit_phase"]
    st.info(f"Ready to file → Episode {episode['number']} → {phase_label}")

    with st.expander("Edit parsed details (optional)"):
        patient_tab,clinical_tab,team_tab=st.tabs(["Patient & filing","Clinical index","Care team"])
        with patient_tab:
            p["full_name"]=st.text_input("Full name",p.get("full_name") or "")
            c1,c2=st.columns(2);p["medical_record_number"]=c1.text_input("RM",p.get("medical_record_number") or "");p["sex"]=c2.text_input("Sex",p.get("sex") or "")
            c1,c2=st.columns(2);p["age"]=c1.number_input("Age",0,150,p.get("age") or 0);p["insurance"]=c2.text_input("Insurance",p.get("insurance") or "")
            c1,c2=st.columns(2);v["location"]=c1.text_input("Care setting",v.get("location") or "");p["hospital"]=c2.text_input("Hospital",p.get("hospital") or "")
            v["visit_date"]=st.text_input("Visit date",v.get("visit_date") or "")
        with clinical_tab:
            d["diagnoses"]=st.text_area("Diagnosis · one per line","\n".join(d.get("diagnoses",[]))).splitlines()
            d["procedures"]=st.text_area("Procedures · one per line","\n".join(d.get("procedures",[]))).splitlines()
            t1,t2,t3=st.tabs(["Subjective","Assessment","Plan"])
            d["soap"]["subjective"]=t1.text_area("Subjective",d["soap"].get("subjective","") or "",height=150,label_visibility="collapsed")
            d["soap"]["assessment"]=t2.text_area("Assessment",d["soap"].get("assessment","") or "",height=150,label_visibility="collapsed")
            d["soap"]["plan"]=t3.text_area("Plan",d["soap"].get("plan","") or "",height=150,label_visibility="collapsed")
        with team_tab:
            dpjp=d.setdefault("dpjp",{}) or {};d["dpjp"]=dpjp
            dpjp["full_name"]=st.text_input("DPJP",dpjp.get("full_name") or "")
            d["operator"]=st.text_input("Operator",d.get("operator") or "")
            d["assistant_operators"]=st.text_area("Assistant operators · one per line","\n".join(d.get("assistant_operators",[]))).splitlines()
    if d["warnings"]:
        with st.expander(f"Review {len(d['warnings'])} parser warning(s)"):
            for w in d["warnings"]:st.warning(w)
    missing=[label for label,value in (("patient name",p.get("full_name")),("RM",p.get("medical_record_number")),("visit date",v.get("visit_date"))) if not value]
    if missing:st.error("Complete before saving: "+", ".join(missing))
    return not missing

def quick_upload():
    page_header("Clinical intake · Plate 01","Archive a surgical visit","Paste the source report, verify the clinical index, then file the record into its exact Drive episode.")
    active=2 if "saved" in st.session_state else 1 if "parsed" in st.session_state else 0;workflow_steps(active)
    if "saved" in st.session_state:
        x=st.session_state.saved
        with st.container(border=True):
            a,b=st.columns([4,1]);a.success(f"Visit saved to Drive · {x['name']} · RM {x['rm']} · {x['phase']}")
            if x.get("patient_match") in {"exact_rm","identity_typo"}:a.caption("Matched to the existing patient folder"+(" · minor identity typo tolerated" if x["patient_match"]=="identity_typo" else " · RM verified"))
            drive_icon_link(x.get("drive_url"),"Open saved folder in Google Drive")
            if x.get("failed"):st.warning(f"{len(x['failed'])} photo(s) failed. Re-select those files to retry.")
        if st.button("＋  START ANOTHER VISIT"):
            for key in ("parsed","saved","soap_input","pasted_photos"):st.session_state.pop(key,None)
            st.rerun()
        return
    if "parsed" not in st.session_state:
        left,right=st.columns([2.15,1],gap="large")
        with left:
            with st.container(border=True):
                st.markdown('<div class="cv-section-title">Paste clinical report</div><div class="cv-section-sub">Include the complete header, SOAP sections, care team, and DPJP.</div>',unsafe_allow_html=True)
                soap=st.text_area("WhatsApp SOAP",height=310,placeholder="Assalamualaikum dokter…\n\nPatient identity / RM…\n\nS: …\nO: …\nA: …\nP: …",key="soap_input",label_visibility="collapsed")
                if st.button("PARSE & REVIEW  →",type="primary",disabled=not soap,use_container_width=True):
                    with st.spinner("Reading SOAP structure…"):result=api("POST","/parser/soap",json={"soap":soap})
                    if result:
                        if EMBEDDED_MODE:result=prepare_drive_defaults(result)
                        st.session_state.parsed=result;st.rerun()
        with right:
            st.markdown('<div class="cv-note"><div class="num">Atlas intake · Plate 01</div><h3>From clinical note to surgical record</h3><p>CaseVault structures the report locally and preserves the original inside your private archive.</p><ul><li>Clinical parsing stays inside the app</li><li>Episode and operative stage remain editable</li><li>Original files are preserved in Drive</li></ul></div>',unsafe_allow_html=True)
    if "parsed" in st.session_state:
        st.markdown("<br>",unsafe_allow_html=True)
        with st.container(border=True):ready_to_save=preview(st.session_state.parsed)
        st.markdown("<br>",unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="cv-section-title">Attachments</div><div class="cv-section-sub">Optional · JPEG, PNG, or WebP. Upload begins only after final confirmation.</div>',unsafe_allow_html=True)
            upload_col,paste_col=st.columns([1.7,1],gap="medium")
            with upload_col:
                photos=st.file_uploader("Drop photos or browse",type=["jpg","jpeg","png","webp"],accept_multiple_files=True)
            with paste_col:
                st.caption("Or copy an image, then paste it here")
                pasted=pasted_photos()
                if pasted and st.button("CLEAR PASTED PHOTOS",use_container_width=True):
                    st.session_state.pop("pasted_photos",None);st.rerun()
            all_photos=list(photos or [])+pasted
            if pasted:st.image([photo.data for photo in pasted],width=130,caption=[photo.name for photo in pasted])
            if all_photos:st.success(f"{len(all_photos)} photo(s) ready for private Drive upload")
        c1,c2=st.columns([1,2])
        if c1.button("←  EDIT ORIGINAL"):
            st.session_state.pop("parsed",None);st.rerun()
        if c2.button("SAVE VISIT TO GOOGLE DRIVE  →",type="primary",use_container_width=True,disabled=not ready_to_save):
            if EMBEDDED_MODE:
                try:
                    with st.spinner("Creating episode and visit folders…"):result=save_visit_to_drive(st.session_state.parsed,all_photos)
                except Exception as exc:st.error(f"Drive save failed: {exc}");result=None
            else:result=api("POST","/visits",json=st.session_state.parsed)
            if result:st.session_state.saved={**result,"name":st.session_state.parsed["patient"]["full_name"],"rm":st.session_state.parsed["patient"]["medical_record_number"],"phase":st.session_state.parsed["visit"].get("visit_phase")};st.rerun()

def patient_detail(patient:dict,metadata_rows:list[dict]):
    if st.button("←  BACK TO PATIENTS"):
        st.session_state.pop("selected_patient_id",None);st.rerun()
    rows=patient_metadata(metadata_rows,patient);rich_patient=(rows[0].get("patient") if rows else {}) or {}
    name=rich_patient.get("full_name") or patient.get("name") or "Patient"
    rm=rich_patient.get("medical_record_number") or patient.get("rm") or "—"
    title=rich_patient.get("title") or patient.get("title")
    sex=rich_patient.get("sex") or patient.get("sex")
    age=rich_patient.get("age") if rich_patient.get("age") is not None else patient.get("age")
    age_unit=rich_patient.get("age_unit") or patient.get("age_unit") or "Tahun"
    insurance=rich_patient.get("insurance") or patient.get("insurance")
    hospital=rich_patient.get("hospital") or patient.get("hospital")
    care_setting=rich_patient.get("care_setting") or patient.get("care_setting")
    page_header(f"Patient dossier · RM {escape(rm)}",escape(name),"A longitudinal surgical record of episodes, visits, diagnoses, procedures, and operative teams.")
    with st.container(border=True):
        left,right=st.columns([4,1])
        left.markdown(f'<div class="cv-profile"><div class="cv-avatar">{initials(name)}</div><div><h2>{escape(name)}</h2><div class="cv-meta">Patient identity</div></div></div>',unsafe_allow_html=True)
        drive_icon_link(patient.get("drive_url"),"Open patient folder in Google Drive")
        details=[("Title",title),("Gender",sex),("Age",f"{age} {age_unit}" if age is not None else None),("Care setting",care_setting),("Hospital",hospital),("Insurance",insurance),("RM",rm)]
        st.markdown("".join(f'<span class="cv-pill"><b>{escape(label)}</b> · {escape(str(value))}</span>' for label,value in details if value),unsafe_allow_html=True)

    try:
        drive=drive_service();episodes=sorted(list_drive_folders(drive,patient["id"]),key=episode_sort_key)
    except Exception as exc:
        st.error(f"Episode read failed: {exc}");return
    episode_ids={row.get("episode_folder_id") for row in rows if row.get("episode_folder_id")}
    metadata_only=[row for row in rows if row.get("episode_folder_id") not in {x.get("id") for x in episodes}]
    total_episode_ids={x.get("id") for x in episodes}|episode_ids
    st.markdown(f'<div class="cv-section-sub">{len(total_episode_ids)} episode(s) · {len(rows)} indexed visit(s)</div>',unsafe_allow_html=True)
    if not episodes and not metadata_only:
        st.markdown('<div class="cv-empty"><div class="icon">◫</div><b>No episode folders found</b><br>This patient folder exists, but it has no readable episode data yet.</div>',unsafe_allow_html=True);return

    for episode_folder in episodes:
        episode_rows=[row for row in rows if row.get("episode_folder_id")==episode_folder.get("id")]
        diagnoses=compact_values(x for row in episode_rows for x in row.get("diagnoses",[]))
        procedures=compact_values(x for row in episode_rows for x in row.get("procedures",[]))
        episode_match=re.match(r"(?i)^EP\s*0*(\d+)",episode_folder.get("name", ""));episode_folio=f"{int(episode_match.group(1)):02d}" if episode_match else "—"
        try:visit_folders=list_drive_folders(drive,episode_folder["id"])
        except Exception:visit_folders=[]
        with st.container(border=True):
            head,open_col=st.columns([4,1])
            head.markdown(f'<div class="cv-episode-head"><div class="cv-case-no">{episode_folio}</div><div><div class="cv-episode-title">{escape(episode_folder.get("name") or "Episode")}</div><div class="cv-episode-meta">{len(visit_folders)} Drive visit folder(s) · {len(episode_rows)} indexed visit(s)</div></div></div>',unsafe_allow_html=True)
            drive_icon_link(episode_folder.get("webViewLink"),"Open episode folder in Google Drive")
            summary_cols=st.columns(2)
            with summary_cols[0]:show_optional_field("Procedures / case",procedures)
            with summary_cols[1]:show_optional_field("Diagnoses",diagnoses)
            if not visit_folders and not episode_rows:st.caption("No visit details are available inside this episode yet.")
            drive_visit_ids={folder.get("id") for folder in visit_folders}
            for visit_folder in sorted(visit_folders,key=lambda x:x.get("name","").casefold(),reverse=True):
                row=next((x for x in episode_rows if x.get("visit_folder_id")==visit_folder.get("id")),None)
                visit=(row or {}).get("visit",{});roles=(row or {}).get("roles",{})
                phase=visit.get("visit_phase");pod=visit.get("pod_roman") or visit.get("pod_number")
                visit_title=" - ".join(compact_values([visit.get("visit_date"),f"POD {pod}" if phase=="POD" and pod is not None else phase]))
                if not visit_title:visit_title=visit_folder.get("name") or "Visit"
                with st.expander(f"{visit_title} - Open record"):
                    title_col,visit_link=st.columns([4,1]);title_col.markdown(f'<div class="cv-meta">Drive folder · {escape(visit_folder.get("name") or "Visit")}</div>',unsafe_allow_html=True)
                    drive_icon_link(visit_folder.get("webViewLink"),"Open visit folder in Google Drive")
                    render_visit_contents(drive,visit_folder,row,f"{episode_folder['id']}_{visit_folder['id']}")
            for row in episode_rows:
                if row.get("visit_folder_id") in drive_visit_ids:continue
                visit=row.get("visit",{});roles=row.get("roles",{})
                with st.expander(f"{visit.get('visit_date') or visit.get('visit_phase') or 'Indexed visit'}  ·  Indexed record"):
                    st.caption("Drive visit folder is unavailable; showing saved metadata.")
                    show_optional_field("Procedure",row.get("procedures",[]));show_optional_field("Diagnosis",row.get("diagnoses",[]));show_optional_field("DPJP",roles.get("dpjp"));show_optional_field("Operator",roles.get("operator"))
                    soap=(row.get("soap") or {}).get("original_soap")
                    if soap:st.code(soap,language=None,wrap_lines=True)

    if metadata_only:
        st.markdown(f"### {len(metadata_only)} additional indexed visit(s)")
        for index,row in enumerate(metadata_only):
            episode=row.get("episode",{});visit=row.get("visit",{});roles=row.get("roles",{})
            with st.expander(f"{episode.get('title') or 'Episode'} · {visit.get('visit_date') or visit.get('visit_phase') or 'Visit'}"):
                show_optional_field("Procedure",row.get("procedures",[]));show_optional_field("Diagnosis",row.get("diagnoses",[]));show_optional_field("Operator",roles.get("operator"))
                soap=(row.get("soap") or {}).get("original_soap")
                if soap:
                    st.code(soap,language=None,wrap_lines=True)
                    st.download_button("DOWNLOAD SOAP",soap,file_name="SOAP.txt",mime="text/plain",key=f"metadata_soap_{index}_{row.get('metadata_file_id','unknown')}")

def patients():
    try:
        if EMBEDDED_MODE:
            drive=drive_service();rows=[patient_from_folder(x) for x in list_drive_folders(drive,drive.root_id)];metadata_rows=list_drive_visit_metadata(drive,drive.root_id)
        else:rows=api("GET","/patients") or [];metadata_rows=[]
    except Exception as exc:st.error(f"Drive read failed: {exc}");return
    selected=st.session_state.get("selected_patient_id")
    if selected:
        patient=next((p for p in rows if p.get("id")==selected),None)
        if patient:patient_detail(patient,metadata_rows);return
        st.session_state.pop("selected_patient_id",None)
    page_header("Case registry · Master index","Surgical dossiers","Browse the living archive by patient, then open a dossier to trace every episode and operative visit.")
    search_col,sync_col=st.columns([5,1]);q=search_col.text_input("Filter patients",placeholder="Search name or medical record number…",label_visibility="collapsed")
    if sync_col.button("↻  SYNC",use_container_width=True):clear_drive_cache();st.rerun()
    if q:rows=[p for p in rows if q.casefold() in f"{p.get('folder_name','')} {p.get('rm','')}".casefold()]
    st.markdown(f'<div class="cv-section-sub">{len(rows)} Drive patient folder(s) · synced just now</div>',unsafe_allow_html=True)
    if not rows:st.markdown('<div class="cv-empty"><div class="icon">◫</div><b>No patient folders found</b><br>Try another search or sync Drive again.</div>',unsafe_allow_html=True);return
    grid=st.columns(2,gap="medium")
    for i,p in enumerate(sorted(rows,key=lambda x:x.get("name","").casefold())):
        patient_rows=patient_metadata(metadata_rows,p);latest=patient_rows[0] if patient_rows else {};episode_count=len({x.get("episode_folder_id") or (x.get("episode") or {}).get("number") for x in patient_rows})
        latest_case=compact_values((latest.get("procedures") or latest.get("diagnoses") or [])[:2]);roles=latest.get("roles",{})
        with grid[i%2]:
            with st.container(border=True):
                st.markdown(f'<div class="cv-card-folio">Case file · {i+1:03d}</div>',unsafe_allow_html=True)
                av,body=st.columns([.75,4]);av.markdown(f'<div class="cv-avatar">{initials(p["name"])}</div>',unsafe_allow_html=True)
                body.markdown(f"<strong>{escape(p['name'])}</strong><div class='cv-meta'>RM {escape(p.get('rm') or '—')} · {escape(p.get('hospital') or 'Hospital not parsed')}</div>",unsafe_allow_html=True)
                chips=compact_values([p.get("sex"),f"{episode_count} episode(s)" if episode_count else "Drive archive",f"{len(patient_rows)} visit(s)" if patient_rows else None])
                st.markdown("".join(f'<span class="cv-pill">{escape(x)}</span>' for x in chips),unsafe_allow_html=True)
                show_optional_field("Latest case",latest_case);show_optional_field("Latest operator",roles.get("operator"))
                if st.button("VIEW CASES  →",key=f"patient_{p['id']}",use_container_width=True):
                    st.session_state.selected_patient_id=p["id"];st.rerun()

def search():
    page_header("Atlas index · Cross-reference","Search the case archive","Cross-reference patients, medical record numbers, diagnoses, procedures, DPJP, operators, and assistants.")
    q=st.text_input("Search",placeholder="Try: odontektomi, an operator name, DPJP, diagnosis, or RM…",label_visibility="collapsed")
    if not q:
        st.markdown('<div class="cv-empty"><div class="icon">⌕</div><b>Search across structured Drive metadata</b><br>New CaseVault uploads are indexed by clinical and care-team fields.</div>',unsafe_allow_html=True);return
    if q and EMBEDDED_MODE:
        try:
            with st.spinner("Searching Drive metadata…"):drive=drive_service();patient_rows=drive_patients();rows=search_catalog(patient_rows,list_drive_visit_metadata(drive,drive.root_id),q)
            st.caption(f"{len(rows)} result(s) for “{q}”")
            for row in rows:
                patient=row.get("patient",{});roles=row.get("roles",{})
                with st.container(border=True):
                    patient_name=escape(patient.get('full_name') or patient.get('name') or 'Patient folder');rm=escape(patient.get('medical_record_number') or patient.get('rm') or '—');phase=escape(row.get('visit',{}).get('visit_phase','Legacy Drive folder'))
                    a,b=st.columns([4,1]);a.markdown(f"<strong>{patient_name}</strong><div class='cv-meta'>RM {rm} · {phase}</div>",unsafe_allow_html=True)
                    if not row.get("legacy"):
                        labels=[f"DPJP · {roles['dpjp']}" if roles.get("dpjp") else None,f"Operator · {roles['operator']}" if roles.get("operator") else None]
                        a.markdown("".join(f'<span class="cv-pill">{escape(x)}</span>' for x in labels if x),unsafe_allow_html=True)
                    url=row.get("visit_drive_url") or row.get("patient_drive_url")
                    drive_icon_link(url,"Open search result in Google Drive")
            if not rows:st.info("No matching Drive metadata. Legacy folders without SOAP metadata can only be searched by patient folder name or RM.")
        except Exception as exc:st.error(f"Drive search failed: {exc}")
    elif q:
        for p in api("GET","/search",params={"q":q}) or []:st.markdown(f'<div class="cv-card"><b>{p["name"]}</b><br><span class="muted">RM {p["rm"]} · {p.get("hospital") or "—"}</span></div>',unsafe_allow_html=True)

def settings_page():
    page_header("Archive control · System plate","Settings & connection","Review the active CaseVault identity, source-of-truth Drive, and the archive privacy boundary.")
    h=api("GET","/health") or {}
    c1,c2,c3=st.columns(3)
    c1.metric("CaseVault service","Online" if h.get("status")=="ok" else "Unavailable")
    c2.metric("App login",(st.session_state.get("app_user") or {}).get("role","Not signed in").title())
    c3.metric("Archive Drive","Connected" if drive_configured() and archive_token_configured() else "Setup needed")
    if EMBEDDED_MODE:
        user=st.session_state.get("app_user") or {}
        with st.container(border=True):
            st.markdown(f"**CaseVault account**  \n{escape(user.get('username','—'))} · {escape(user.get('role','—'))}")
            st.caption("User and admin currently have the same capabilities. Role-based restrictions can be enabled later.")
        if setting("CASEVAULT_USER_PASSWORD","user")=="user" or setting("CASEVAULT_ADMIN_PASSWORD","admin")=="admin":
            st.warning("Temporary default credentials are active: user/user and admin/admin. Change both passwords in Streamlit Secrets before clinical use.")
        if not drive_configured():st.warning("GOOGLE_DRIVE_ROOT_FOLDER_ID belum diisi.")
        if not archive_token_configured():
            st.warning("Archive account belum terhubung. Lakukan sekali sebagai admin; pengguna lain tetap login dengan password CaseVault.")
            if user.get("role")=="admin":
                if not archive_oauth_configured():st.error("Tambahkan GOOGLE_CLIENT_ID dan GOOGLE_CLIENT_SECRET ke Streamlit Secrets terlebih dahulu.")
                else:begin_archive_connect()
        else:
            st.success("Archive account connected. Patient folders and uploads use the configured Google Drive automatically.")
        generated=st.session_state.get("generated_archive_refresh_token")
        if generated and user.get("role")=="admin":
            st.error("One final setup step: copy the secret below into Streamlit App → Settings → Secrets, then save. Treat this token like a password.")
            st.code(f'GOOGLE_ARCHIVE_REFRESH_TOKEN = "{generated}"',language="toml",wrap_lines=True)
        if st.button("SIGN OUT OF CASEVAULT"):
            for key in ("app_user","selected_patient_id","parsed","saved","pasted_photos","generated_archive_refresh_token","archive_google_credentials"):st.session_state.pop(key,None)
            st.rerun()
    else:st.markdown(f"[Sign in with Google]({API}/auth/login)")
    st.info("CaseVault never creates public sharing links and sends no clinical data to AI services. The archive Google account is the Drive identity; app users never grant personal Drive access.")

page=sidebar()
if EMBEDDED_MODE and not st.session_state.get("app_user"):
    login_screen()
else:
    {"＋  Case intake":quick_upload,"◫  Case registry":patients,"⌕  Atlas index":search,"⚙  Archive control":settings_page}[page]()
