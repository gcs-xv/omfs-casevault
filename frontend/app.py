from __future__ import annotations
import os
import sys
import io
import base64
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import httpx
import streamlit as st
from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

try:
    _embedded_default=st.secrets.get("EMBEDDED_MODE","false")
except FileNotFoundError:
    _embedded_default="false"
EMBEDDED_MODE=str(os.getenv("EMBEDDED_MODE",_embedded_default)).lower()=="true"
if EMBEDDED_MODE:
    from backend.services.drive_catalog import patient_from_folder,search_catalog
    from backend.services.drive_service import DriveService
    from backend.services.soap_parser import parse_soap
    from backend.utils.normalization import normalize_rm,safe_name

API=os.getenv("API_URL","http://127.0.0.1:8000")
APP_URL="https://omfs-casevault-dj7trsufq6jykaeuddo7b4.streamlit.app/oauth2callback"
DRIVE_SCOPE="https://www.googleapis.com/auth/drive"
EMAIL_SCOPE="https://www.googleapis.com/auth/userinfo.email"
PROFILE_SCOPE="https://www.googleapis.com/auth/userinfo.profile"

def setting(name,default=""):
    value=os.getenv(name)
    if value is not None:return value
    try:return st.secrets.get(name,default)
    except FileNotFoundError:return default

def oauth_configured():
    return bool(setting("GOOGLE_CLIENT_ID") and setting("GOOGLE_CLIENT_SECRET"))

def drive_configured():
    return bool(setting("GOOGLE_DRIVE_ROOT_FOLDER_ID"))

def oauth_flow(state=None):
    config={"web":{"client_id":setting("GOOGLE_CLIENT_ID"),"client_secret":setting("GOOGLE_CLIENT_SECRET"),"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[setting("GOOGLE_REDIRECT_URI",APP_URL)]}}
    return Flow.from_client_config(config,scopes=["openid",EMAIL_SCOPE,PROFILE_SCOPE,DRIVE_SCOPE],state=state,redirect_uri=setting("GOOGLE_REDIRECT_URI",APP_URL))

def state_signer():
    return URLSafeTimedSerializer(setting("SESSION_SECRET","casevault-change-me"),salt="casevault-google-oauth")

def session_cipher():
    key=base64.urlsafe_b64encode(hashlib.sha256(setting("SESSION_SECRET","casevault-change-me").encode()).digest())
    return Fernet(key)

def oauth_session_db():
    Path("data").mkdir(exist_ok=True)
    db=sqlite3.connect("data/casevault.db")
    db.execute("CREATE TABLE IF NOT EXISTS oauth_sessions (id TEXT PRIMARY KEY, payload BLOB NOT NULL, expires_at INTEGER NOT NULL)")
    db.execute("DELETE FROM oauth_sessions WHERE expires_at < ?",(int(time.time()),));db.commit()
    return db

def persist_google_session(credentials,user):
    session_id=secrets.token_urlsafe(32)
    payload=session_cipher().encrypt(json.dumps({"credentials":json.loads(credentials.to_json()),"user":user}).encode())
    with oauth_session_db() as db:db.execute("INSERT INTO oauth_sessions VALUES (?,?,?)",(session_id,payload,int(time.time())+8*60*60))
    return session_id

def restore_google_session():
    if st.session_state.get("google_user"):return
    session_id=st.query_params.get("cv_session")
    if not session_id:return
    try:
        with oauth_session_db() as db:row=db.execute("SELECT payload FROM oauth_sessions WHERE id=? AND expires_at>=?",(session_id,int(time.time()))).fetchone()
        if not row:raise InvalidToken
        saved=json.loads(session_cipher().decrypt(row[0]).decode())
        st.session_state.google_credentials=Credentials.from_authorized_user_info(saved["credentials"])
        st.session_state.google_user=saved["user"]
    except (InvalidToken,ValueError,KeyError):
        st.query_params.pop("cv_session",None)

def begin_google_login():
    state=state_signer().dumps({"nonce":secrets.token_urlsafe(18)})
    url,_=oauth_flow(state).authorization_url(access_type="offline",prompt="consent")
    st.link_button("SIGN IN WITH GOOGLE",url,type="primary",use_container_width=True)

def finish_google_login():
    code=st.query_params.get("code");state=st.query_params.get("state")
    if not code:return
    try:
        state_signer().loads(state,max_age=600)
        flow=oauth_flow(state);flow.fetch_token(code=code)
        info=id_token.verify_oauth2_token(flow.credentials.id_token,GoogleRequest(),setting("GOOGLE_CLIENT_ID"))
        allowed={x.strip().lower() for x in setting("ALLOWED_GOOGLE_EMAILS").split(",") if x.strip()}
        if allowed and info.get("email","").lower() not in allowed:raise PermissionError("Akun Google ini tidak diizinkan.")
        user={"email":info["email"],"name":info.get("name",info["email"])}
        session_id=persist_google_session(flow.credentials,user)
        st.query_params.clear();st.query_params["cv_session"]=session_id;st.rerun()
    except (BadSignature,SignatureExpired):st.error("Login kedaluwarsa atau tidak valid. Silakan login ulang.")
    except Exception as exc:st.error(f"Google login gagal: {exc}")

def current_credentials():
    credentials=st.session_state.get("google_credentials")
    if credentials and credentials.expired and credentials.refresh_token:credentials.refresh(GoogleRequest())
    return credentials

def drive_service():
    credentials=current_credentials()
    if not credentials:raise ValueError("Silakan login dengan Google terlebih dahulu.")
    return DriveService(credentials,setting("GOOGLE_DRIVE_ROOT_FOLDER_ID"))

def drive_patients():
    drive=drive_service()
    return [patient_from_folder(x) for x in drive.list_folders(drive.root_id)]

def int_to_roman(number:int)->str:
    pairs=((1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I"));out=[]
    for value,symbol in pairs:
        while number>=value:out.append(symbol);number-=value
    return "".join(out)

def patient_folder_name(patient:dict)->str:
    identity=" ".join(x for x in [patient.get("title"),patient.get("full_name")] if x).strip()
    age=f"{patient.get('age')} {patient.get('age_unit') or 'Tahun'}" if patient.get("age") else None
    parts=[identity,patient.get("sex"),age,patient.get("insurance"),patient.get("hospital"),f"RM {patient.get('medical_record_number')}"]
    return " / ".join(str(x) for x in parts if x)

def save_visit_to_drive(data:dict,photos:list)->dict:
    drive=drive_service();patient=data["patient"];visit=data["visit"];episode=data["episode"]
    rm=normalize_rm(patient.get("medical_record_number"))
    if not rm:raise ValueError("Nomor RM wajib diisi.")
    matches=[p for p in drive_patients() if p["rm_normalized"]==rm]
    if matches:
        patient_folder_id=matches[0]["id"];patient_drive_url=matches[0]["drive_url"]
    else:
        created=drive.create_folder(patient_folder_name(patient),drive.root_id);patient_folder_id=created.id;patient_drive_url=created.url
    episode_number=int(episode["number"]);prefix=f"EP{episode_number:02d}"
    episode_folders=drive.list_folders(patient_folder_id)
    existing=next((x for x in episode_folders if x["name"].upper().startswith(prefix)),None)
    if existing:
        episode_folder_id=existing["id"]
    else:
        episode_folder_id=drive.create_folder(f"{prefix} - {episode['title'] or 'Clinical Episode'}",patient_folder_id).id
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
    metadata={"schema_version":1,"casevault_root_id":drive.root_id,"patient":patient,"patient_folder_id":patient_folder_id,"patient_drive_url":patient_drive_url,"episode":episode,"episode_folder_id":episode_folder_id,"visit":visit,"visit_folder_id":visit_folder.id,"visit_drive_url":visit_folder.url,"diagnoses":data.get("diagnoses",[]),"procedures":data.get("procedures",[]),"roles":roles,"search_blob":search_blob,"saved_at":datetime.utcnow().isoformat()+"Z","saved_by":st.session_state.google_user["email"]}
    drive.upload_bytes("casevault-metadata.json",json.dumps(metadata,ensure_ascii=False,indent=2).encode("utf-8"),"application/json",visit_folder.id,{"casevault_root":drive.root_id,"casevault_type":"visit_metadata"})
    uploaded=[];failed=[]
    for i,file in enumerate(photos,1):
        try:
            name=f"{i:03d}_{safe_name(file.name)}";drive.upload_bytes(name,file.getvalue(),file.type,visit_folder.id);uploaded.append(name)
        except Exception as exc:failed.append({"file":file.name,"error":str(exc)})
    return {"uploaded":uploaded,"failed":failed,"drive_url":visit_folder.url,"patient_drive_url":patient_drive_url,"save_state":"complete" if not failed else "partial_failure"}

finish_google_login()
restore_google_session()
st.set_page_config(page_title="OMFS CaseVault",page_icon="🗂️",layout="wide",initial_sidebar_state="expanded")
if st.query_params.get("session_token"):
    st.session_state.api_session_token=st.query_params["session_token"]
    st.query_params.clear()
st.markdown("""<style>
[data-testid="stSidebar"]{background:#111d2e;color:#e9eef5}[data-testid="stSidebar"] *{color:#e9eef5}
.block-container{max-width:1440px;padding-top:2rem}.cv-card{border:1px solid #dce3ea;border-radius:14px;padding:18px;background:#fff;margin:8px 0}
.eyebrow{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:#557087;font-weight:700}.muted{color:#66788a}.ok{color:#16794b}.warn{color:#a05a00}
h1,h2,h3{color:#17283b}div.stButton>button[kind="primary"]{background:#176b67;border-color:#176b67;border-radius:9px;font-weight:700}
</style>""",unsafe_allow_html=True)

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
    if path=="/health":return {"status":"ok","auth_configured":oauth_configured(),"drive_configured":drive_configured(),"signed_in":bool(st.session_state.get("google_user")),"mode":"Streamlit Cloud"}
    if path=="/parser/soap":return parse_soap(kwargs["json"]["soap"])
    raise ValueError(f"Unsupported embedded route: {method} {path}")

def sidebar():
    st.sidebar.markdown("## OMFS CaseVault\nClinical Case Archive")
    page=st.sidebar.radio("Navigation",["Quick Upload","Patients","Search","Settings"],label_visibility="collapsed")
    st.sidebar.markdown("---")
    health=api("GET","/health")
    if health:
        st.sidebar.caption("● Service connected")
        if not health["auth_configured"]:st.sidebar.warning("Google OAuth needs setup")
        elif EMBEDDED_MODE and st.session_state.get("google_user"):
            st.sidebar.success(f"Google: {st.session_state.google_user['email']}")
    return page

def preview(d):
    p=d["patient"];v=d["visit"]
    st.markdown('<div class="eyebrow">Parse preview · edit before save</div>',unsafe_allow_html=True)
    a,b=st.columns(2)
    with a:
        st.markdown("#### Patient")
        p["full_name"]=st.text_input("Full name",p.get("full_name") or "")
        c1,c2=st.columns(2);p["medical_record_number"]=c1.text_input("RM",p.get("medical_record_number") or "");p["sex"]=c2.text_input("Sex",p.get("sex") or "")
        c1,c2=st.columns(2);p["age"]=c1.number_input("Age",0,150,p.get("age") or 0);p["insurance"]=c2.text_input("Insurance",p.get("insurance") or "")
        p["hospital"]=st.text_input("Hospital",p.get("hospital") or "")
    with b:
        st.markdown("#### Episode & visit")
        episode=d.setdefault("episode",{})
        c1,c2=st.columns(2);episode["number"]=c1.number_input("Episode number",1,999,int(episode.get("number") or 1));episode["title"]=c2.text_input("Episode title",episode.get("title") or ((d.get("diagnoses") or d.get("procedures") or ["Clinical Episode"])[0]))
        v["visit_date"]=st.text_input("Date (YYYY-MM-DD)",v.get("visit_date") or "")
        phases=["Terjaring","Pre-op","Intra-op","POD"];suggested=v.get("visit_phase") if v.get("visit_phase") in phases else "Terjaring"
        v["visit_phase"]=st.selectbox("Visit stage",phases,index=phases.index(suggested),help="Pilih manual; CaseVault tidak akan menganggap kunjungan non-POD sebagai POD 0.")
        if v["visit_phase"]=="POD":
            c1,c2=st.columns(2);v["pod_number"]=c1.number_input("POD number",0,1000,int(v.get("pod_number") or 0));v["pod_roman"]=c2.text_input("POD Roman",v.get("pod_roman") or int_to_roman(int(v["pod_number"])))
        else:v["pod_number"]=None;v["pod_roman"]=None
        d["diagnoses"]=st.text_area("Diagnosis · one per line","\n".join(d.get("diagnoses",[]))).splitlines()
        d["procedures"]=st.text_area("Procedures · one per line","\n".join(d.get("procedures",[]))).splitlines()
        d["operator"]=st.text_input("Operator",d.get("operator") or "")
        d["assistant_operators"]=st.text_area("Assistant operators · one per line","\n".join(d.get("assistant_operators",[]))).splitlines()
    if d["warnings"]:
        for w in d["warnings"]:st.warning(w)
    st.markdown("#### SOAP")
    t1,t2,t3=st.tabs(["Subjective","Assessment","Plan"])
    d["soap"]["subjective"]=t1.text_area("Subjective",d["soap"].get("subjective","") or "",label_visibility="collapsed")
    d["soap"]["assessment"]=t2.text_area("Assessment",d["soap"].get("assessment","") or "",label_visibility="collapsed")
    d["soap"]["plan"]=t3.text_area("Plan",d["soap"].get("plan","") or "",label_visibility="collapsed")

def quick_upload():
    st.markdown("# New clinical visit")
    st.caption("Paste the WhatsApp SOAP, review what was recognized, add photos, and save.")
    soap=st.text_area("WhatsApp SOAP",height=260,placeholder="Paste the complete SOAP here…",key="soap_input")
    if st.button("PARSE SOAP",type="primary",disabled=not soap):
        result=api("POST","/parser/soap",json={"soap":soap})
        if result:st.session_state.parsed=result
    if "parsed" in st.session_state:
        st.divider();preview(st.session_state.parsed)
        photos=st.file_uploader("Clinical photos",type=["jpg","jpeg","png","webp"],accept_multiple_files=True,help="1–30 photos; upload begins only when you save")
        if photos:st.caption(f"{len(photos)} photo(s) ready · originals remain private in Google Drive")
        if st.button("SAVE VISIT",type="primary",use_container_width=True):
            if EMBEDDED_MODE:
                try:result=save_visit_to_drive(st.session_state.parsed,photos or [])
                except Exception as exc:st.error(f"Drive save failed: {exc}");result=None
            else:result=api("POST","/visits",json=st.session_state.parsed)
            if result:st.session_state.saved={**result,"name":st.session_state.parsed["patient"]["full_name"],"rm":st.session_state.parsed["patient"]["medical_record_number"],"phase":st.session_state.parsed["visit"].get("visit_phase")}
    if "saved" in st.session_state:
        x=st.session_state.saved;st.success(f"✓ SAVED TO DRIVE — {x['name']} · RM {x['rm']} · {x['phase']} · {len(x.get('uploaded',[]))} photos uploaded")
        if x.get("failed"):st.warning(f"{len(x['failed'])} photo(s) failed. Re-select only those files to retry.")
        if x.get("drive_url"):st.link_button("OPEN DRIVE FOLDER",x["drive_url"])

def patients():
    st.markdown("# Patients");st.caption("Live from Google Drive · refresh the page after changing folders in Drive")
    if st.button("SYNC NOW",use_container_width=False):st.rerun()
    try:rows=drive_patients() if EMBEDDED_MODE else (api("GET","/patients") or [])
    except Exception as exc:st.error(f"Drive read failed: {exc}");return
    q=st.text_input("Filter patients",placeholder="Name or RM")
    if q:rows=[p for p in rows if q.casefold() in f"{p.get('folder_name','')} {p.get('rm','')}".casefold()]
    st.caption(f"{len(rows)} patient folder(s)")
    for p in rows:
        with st.container(border=True):
            a,b=st.columns([4,1]);a.markdown(f"**{p['name']}**  \nRM {p.get('rm') or '—'} · {p.get('sex') or '—'} · {p.get('hospital') or '—'}")
            if p.get("drive_url"):b.link_button("OPEN DRIVE",p["drive_url"],use_container_width=True)

def search():
    st.markdown("# Search");q=st.text_input("Patient, RM, diagnosis, procedure, DPJP, operator, or assistant operator")
    if q and EMBEDDED_MODE:
        try:
            drive=drive_service();patient_rows=drive_patients();rows=search_catalog(patient_rows,drive.list_visit_metadata(),q)
            for row in rows:
                patient=row.get("patient",{});roles=row.get("roles",{})
                with st.container(border=True):
                    st.markdown(f"**{patient.get('full_name') or patient.get('name') or 'Patient folder'}**  \nRM {patient.get('medical_record_number') or patient.get('rm') or '—'} · {row.get('visit',{}).get('visit_phase','Legacy Drive folder')}")
                    if not row.get("legacy"):st.caption(" · ".join(x for x in [roles.get("dpjp"),roles.get("operator"),", ".join(roles.get("assistant_operators",[]))] if x))
                    url=row.get("visit_drive_url") or row.get("patient_drive_url")
                    if url:st.link_button("OPEN DRIVE",url)
            if not rows:st.info("No matching Drive metadata. Legacy folders without SOAP metadata can only be searched by patient folder name or RM.")
        except Exception as exc:st.error(f"Drive search failed: {exc}")
    elif q:
        for p in api("GET","/search",params={"q":q}) or []:st.markdown(f'<div class="cv-card"><b>{p["name"]}</b><br><span class="muted">RM {p["rm"]} · {p.get("hospital") or "—"}</span></div>',unsafe_allow_html=True)

def settings_page():
    st.markdown("# Settings & setup")
    h=api("GET","/health") or {};st.json(h)
    if EMBEDDED_MODE:
        if not oauth_configured():st.warning("Tambahkan GOOGLE_CLIENT_ID dan GOOGLE_CLIENT_SECRET ke Streamlit Secrets.")
        elif not st.session_state.get("google_user"):begin_google_login()
        else:
            st.success(f"Terhubung sebagai {st.session_state.google_user['email']}")
            if st.button("SIGN OUT"):
                session_id=st.query_params.get("cv_session")
                if session_id:
                    with oauth_session_db() as db:db.execute("DELETE FROM oauth_sessions WHERE id=?",(session_id,))
                st.session_state.pop("google_credentials",None);st.session_state.pop("google_user",None);st.query_params.clear();st.rerun()
        if not drive_configured():st.warning("GOOGLE_DRIVE_ROOT_FOLDER_ID belum diisi.")
    else:st.markdown(f"[Sign in with Google]({API}/auth/login)")
    st.info("OAuth uses Drive's app-created-files scope. CaseVault never creates public sharing links and sends no clinical data to AI services.")

page=sidebar()
if EMBEDDED_MODE and oauth_configured() and not st.session_state.get("google_user"):
    st.markdown("# Sign in to CaseVault")
    st.caption("Gunakan akun Google yang diizinkan untuk membuka arsip dan menghubungkan Google Drive.")
    begin_google_login()
    st.info("CaseVault meminta akses Drive karena folder tujuan sudah ada. Tidak ada file yang dibuat publik.")
else:
    {"Quick Upload":quick_upload,"Patients":patients,"Search":search,"Settings":settings_page}[page]()
