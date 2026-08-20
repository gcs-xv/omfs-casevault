from __future__ import annotations
import os
import sys
import io
import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime
from html import escape
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

def prepare_drive_defaults(data:dict)->dict:
    """Choose fast filing defaults while keeping them editable."""
    episode=data.setdefault("episode",{});visit=data["visit"]
    episode.setdefault("title",(data.get("diagnoses") or data.get("procedures") or ["Clinical Episode"])[0])
    existing=[];patient_found=False
    try:
        rm=normalize_rm(data["patient"].get("medical_record_number"));drive=drive_service()
        match=next((p for p in drive_patients() if p["rm_normalized"]==rm),None)
        if match:
            patient_found=True
            for folder in drive.list_folders(match["id"]):
                number=re.match(r"(?i)^EP\s*0*(\d+)",folder["name"])
                if number:existing.append(int(number.group(1)))
    except Exception:
        pass
    existing=sorted(set(existing));phase=visit.get("visit_phase") or "Terjaring"
    suggested=(max(existing)+1 if phase=="Terjaring" and existing else max(existing) if existing else 1)
    episode.setdefault("number",suggested);data["_existing_episode_numbers"]=existing;data["_drive_patient_found"]=patient_found
    if phase=="POD" and visit.get("pod_number") is not None:visit["pod_roman"]=int_to_roman(int(visit["pod_number"]))
    return data

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
    if not visit.get("visit_date"):raise ValueError("Tanggal kunjungan wajib diisi.")
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
:root{--ink:#12263a;--muted:#687b8e;--line:#dfe8ed;--paper:#fff;--wash:#f4f8f8;--teal:#087f78;--teal2:#0a655f;--mint:#e2f3ef;--gold:#dca44b}
.stApp{background:radial-gradient(circle at 82% 0%,#e7f5f1 0,transparent 27rem),linear-gradient(180deg,#f8fbfb 0,#f2f6f7 100%);color:var(--ink)}
.block-container{max-width:1320px;padding:2.2rem 2.2rem 5rem}.main .block-container{animation:cvfade .25s ease-out}@keyframes cvfade{from{opacity:.3;transform:translateY(4px)}to{opacity:1;transform:none}}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#10283a 0%,#0c1c2a 100%);border-right:1px solid #1c3c50}
[data-testid="stSidebar"] *{color:#eaf5f5}[data-testid="stSidebar"] [role="radiogroup"]{gap:.35rem}
[data-testid="stSidebar"] label[data-baseweb="radio"]{padding:.72rem .8rem;border-radius:10px;transition:.15s;background:transparent}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked){background:#1b4653;border:1px solid #2e6070}
.cv-brand{display:flex;gap:.75rem;align-items:center;padding:.25rem 0 1.2rem}.cv-logo{width:42px;height:42px;border-radius:13px;background:linear-gradient(145deg,#35c4b4,#0a766f);display:grid;place-items:center;font-weight:850;font-size:1.15rem;color:white;box-shadow:0 10px 25px #06131f66}.cv-brand strong{display:block;font-size:1.05rem}.cv-brand span{font-size:.72rem;color:#a9c6ce;letter-spacing:.08em;text-transform:uppercase}
.cv-hero{padding:1.25rem 0 1.65rem;border-bottom:1px solid var(--line);margin-bottom:1.4rem}.cv-kicker{color:var(--teal);font-size:.72rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase;margin-bottom:.45rem}.cv-hero h1{font-size:2.15rem;line-height:1.1;letter-spacing:-.035em;margin:0;color:var(--ink)}.cv-hero p{color:var(--muted);font-size:1rem;margin:.55rem 0 0;max-width:760px}
.cv-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:.65rem;margin:.2rem 0 1.35rem}.cv-step{display:flex;align-items:center;gap:.65rem;background:#fff;border:1px solid var(--line);border-radius:13px;padding:.72rem .85rem;color:#789}.cv-step b{width:27px;height:27px;border-radius:50%;display:grid;place-items:center;background:#edf3f4;color:#607785;font-size:.78rem}.cv-step.active{border-color:#72bcb5;background:#f2fbf8;color:var(--ink);box-shadow:0 6px 20px #155b4f0c}.cv-step.active b{background:var(--teal);color:#fff}.cv-step.done b{background:#d9f1eb;color:#087065}
.cv-note{background:linear-gradient(145deg,#102d3d,#163f4b);border-radius:18px;padding:1.3rem 1.35rem;color:#eaf7f5;min-height:190px;box-shadow:0 16px 38px #16374418}.cv-note .num{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:#8ecbc3}.cv-note h3{color:white;margin:.5rem 0}.cv-note p{color:#bfd5d8;font-size:.9rem}.cv-note ul{padding-left:1.15rem;color:#d9ebec;font-size:.87rem}
.cv-avatar{width:46px;height:46px;border-radius:14px;background:linear-gradient(145deg,#dff2ed,#cbe9e4);color:#126e68;display:grid;place-items:center;font-weight:800;letter-spacing:.04em}.cv-meta{color:var(--muted);font-size:.86rem;margin-top:.25rem}.cv-pill{display:inline-block;padding:.2rem .5rem;border-radius:999px;background:#e7f3f0;color:#176c66;font-size:.7rem;font-weight:750;margin:.15rem .25rem .1rem 0}.cv-empty{text-align:center;padding:3rem 1rem;background:#fff;border:1px dashed #cbdade;border-radius:18px;color:var(--muted)}.cv-empty .icon{font-size:2rem;margin-bottom:.6rem}.cv-section-title{font-size:1rem;font-weight:780;color:var(--ink);margin:.2rem 0 .2rem}.cv-section-sub{color:var(--muted);font-size:.84rem;margin-bottom:.8rem}
[data-testid="stVerticalBlockBorderWrapper"]{background:rgba(255,255,255,.88);border-color:var(--line)!important;border-radius:16px!important;box-shadow:0 8px 28px #17354b08}
[data-baseweb="input"]>div,[data-baseweb="textarea"]>div,[data-baseweb="select"]>div{border-color:#d6e2e6!important;border-radius:11px!important;background:#fff!important}input,textarea{color:var(--ink)!important}
[data-baseweb="tab-list"]{gap:.4rem;border-bottom:1px solid var(--line)}[data-baseweb="tab"]{border-radius:9px 9px 0 0;padding:.65rem 1rem}[aria-selected="true"][data-baseweb="tab"]{color:var(--teal)!important;background:#ecf7f4}
div.stButton>button,div.stLinkButton>a{border-radius:10px;min-height:2.75rem;font-weight:720;transition:.18s}div.stButton>button:hover,div.stLinkButton>a:hover{transform:translateY(-1px)}div.stButton>button[kind="primary"]{background:linear-gradient(135deg,#0a8d84,#086e68);border:none;box-shadow:0 8px 20px #087f7828;color:#fff}
[data-testid="stFileUploaderDropzone"]{background:#f7fbfa;border:1px dashed #91beb7;border-radius:14px}hr{border-color:var(--line)}
@media(max-width:760px){.block-container{padding:1.25rem 1rem 4rem}.cv-hero h1{font-size:1.7rem}.cv-steps{grid-template-columns:1fr}.cv-step{padding:.55rem .7rem}}
</style>""",unsafe_allow_html=True)

def page_header(kicker:str,title:str,subtitle:str):
    st.markdown(f'<div class="cv-hero"><div class="cv-kicker">{kicker}</div><h1>{title}</h1><p>{subtitle}</p></div>',unsafe_allow_html=True)

def workflow_steps(active:int):
    labels=("Paste SOAP","Review details","Save to Drive")
    html="".join(f'<div class="cv-step {"done" if i<active else "active" if i==active else ""}"><b>{"✓" if i<active else i+1}</b><span>{label}</span></div>' for i,label in enumerate(labels))
    st.markdown(f'<div class="cv-steps">{html}</div>',unsafe_allow_html=True)

def initials(name:str)->str:
    words=[x for x in name.replace(".","").split() if x]
    return "".join(x[0].upper() for x in words[-2:]) if words else "CV"

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
    st.sidebar.markdown('<div class="cv-brand"><div class="cv-logo">CV</div><div><strong>OMFS CaseVault</strong><span>Clinical archive</span></div></div>',unsafe_allow_html=True)
    page=st.sidebar.radio("Navigation",["＋  New visit","◫  Patient folders","⌕  Clinical search","⚙  Settings"],label_visibility="collapsed")
    st.sidebar.markdown("---")
    health=api("GET","/health")
    if health:
        st.sidebar.caption("●  CASEVAULT ONLINE")
        if not health["auth_configured"]:st.sidebar.warning("Google OAuth needs setup")
        elif EMBEDDED_MODE and st.session_state.get("google_user"):
            st.sidebar.markdown(f"<small>Signed in as</small><br><b>{st.session_state.google_user['email']}</b>",unsafe_allow_html=True)
    return page

def preview(d):
    p=d["patient"];v=d["visit"]
    episode=d.setdefault("episode",{});existing=d.get("_existing_episode_numbers",[])
    st.markdown('<div class="cv-section-title">Quick check</div><div class="cv-section-sub">CaseVault filled the details automatically. Change only what is wrong.</div>',unsafe_allow_html=True)
    identity_status="Existing Drive patient" if d.get("_drive_patient_found") else "New patient folder"
    st.markdown(f'<div style="display:flex;align-items:center;gap:.8rem;margin:.2rem 0 1rem"><div class="cv-avatar">{initials(p.get("full_name") or "CV")}</div><div><strong>{escape(p.get("full_name") or "Patient not detected")}</strong><div class="cv-meta">RM {escape(p.get("medical_record_number") or "not detected")} · {escape(identity_status)} · {escape(v.get("visit_date") or "date missing")}</div></div></div>',unsafe_allow_html=True)
    episode_choices=sorted(set(existing+[max(existing)+1 if existing else 1,int(episode.get("number") or 1)]))
    def episode_label(number):return f"Episode {number} · {'existing' if number in existing else 'new'}"
    c1,c2=st.columns(2)
    episode["number"]=c1.selectbox("File into",episode_choices,index=episode_choices.index(int(episode.get("number") or episode_choices[0])),format_func=episode_label)
    phases=["Terjaring","Pre-op","Intra-op","POD"];suggested=v.get("visit_phase") if v.get("visit_phase") in phases else "Terjaring"
    v["visit_phase"]=c2.selectbox("Visit stage",phases,index=phases.index(suggested))
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
            p["hospital"]=st.text_input("Hospital",p.get("hospital") or "")
            c1,c2=st.columns(2);v["visit_date"]=c1.text_input("Visit date",v.get("visit_date") or "");episode["title"]=c2.text_input("Episode title",episode.get("title") or "Clinical Episode")
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
    page_header("Quick capture","Archive a clinical visit","Paste the WhatsApp report, verify the structured fields, then file everything to the correct Drive episode.")
    active=2 if "saved" in st.session_state else 1 if "parsed" in st.session_state else 0;workflow_steps(active)
    if "saved" in st.session_state:
        x=st.session_state.saved
        with st.container(border=True):
            a,b=st.columns([4,1]);a.success(f"Visit saved to Drive · {x['name']} · RM {x['rm']} · {x['phase']}")
            if x.get("drive_url"):b.link_button("OPEN FOLDER ↗",x["drive_url"],use_container_width=True)
            if x.get("failed"):st.warning(f"{len(x['failed'])} photo(s) failed. Re-select those files to retry.")
        if st.button("＋  START ANOTHER VISIT"):
            for key in ("parsed","saved","soap_input"):st.session_state.pop(key,None)
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
            st.markdown('<div class="cv-note"><div class="num">Private workflow</div><h3>From message to archive</h3><p>CaseVault recognizes the clinical structure locally, then lets you stay in control.</p><ul><li>No AI service receives the SOAP</li><li>Choose episode and visit stage</li><li>Save originals directly to Drive</li></ul></div>',unsafe_allow_html=True)
    if "parsed" in st.session_state:
        st.markdown("<br>",unsafe_allow_html=True)
        with st.container(border=True):ready_to_save=preview(st.session_state.parsed)
        st.markdown("<br>",unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="cv-section-title">Attachments</div><div class="cv-section-sub">Optional · JPEG, PNG, or WebP. Upload begins only after final confirmation.</div>',unsafe_allow_html=True)
            photos=st.file_uploader("Clinical photos",type=["jpg","jpeg","png","webp"],accept_multiple_files=True,label_visibility="collapsed")
            if photos:st.success(f"{len(photos)} photo(s) ready for private Drive upload")
        c1,c2=st.columns([1,2])
        if c1.button("←  EDIT ORIGINAL"):
            st.session_state.pop("parsed",None);st.rerun()
        if c2.button("SAVE VISIT TO GOOGLE DRIVE  →",type="primary",use_container_width=True,disabled=not ready_to_save):
            if EMBEDDED_MODE:
                try:
                    with st.spinner("Creating episode and visit folders…"):result=save_visit_to_drive(st.session_state.parsed,photos or [])
                except Exception as exc:st.error(f"Drive save failed: {exc}");result=None
            else:result=api("POST","/visits",json=st.session_state.parsed)
            if result:st.session_state.saved={**result,"name":st.session_state.parsed["patient"]["full_name"],"rm":st.session_state.parsed["patient"]["medical_record_number"],"phase":st.session_state.parsed["visit"].get("visit_phase")};st.rerun()

def patients():
    page_header("Drive catalog","Patient folders","A live view of your existing Google Drive archive. Nothing here is copied into local patient storage.")
    search_col,sync_col=st.columns([5,1]);q=search_col.text_input("Filter patients",placeholder="Search name or medical record number…",label_visibility="collapsed")
    if sync_col.button("↻  SYNC",use_container_width=True):st.rerun()
    try:rows=drive_patients() if EMBEDDED_MODE else (api("GET","/patients") or [])
    except Exception as exc:st.error(f"Drive read failed: {exc}");return
    if q:rows=[p for p in rows if q.casefold() in f"{p.get('folder_name','')} {p.get('rm','')}".casefold()]
    st.markdown(f'<div class="cv-section-sub">{len(rows)} Drive patient folder(s) · synced just now</div>',unsafe_allow_html=True)
    if not rows:st.markdown('<div class="cv-empty"><div class="icon">◫</div><b>No patient folders found</b><br>Try another search or sync Drive again.</div>',unsafe_allow_html=True);return
    grid=st.columns(2,gap="medium")
    for i,p in enumerate(sorted(rows,key=lambda x:x.get("name","").casefold())):
        with grid[i%2]:
            with st.container(border=True):
                av,body=st.columns([.75,4]);av.markdown(f'<div class="cv-avatar">{initials(p["name"])}</div>',unsafe_allow_html=True)
                body.markdown(f"<strong>{escape(p['name'])}</strong><div class='cv-meta'>RM {escape(p.get('rm') or '—')} · {escape(p.get('hospital') or 'Hospital not parsed')}</div>",unsafe_allow_html=True)
                st.markdown(f'<span class="cv-pill">{escape(p.get("sex") or "Sex —")}</span><span class="cv-pill">Google Drive</span>',unsafe_allow_html=True)
                if p.get("drive_url"):st.link_button("OPEN PATIENT FOLDER  ↗",p["drive_url"],use_container_width=True)

def search():
    page_header("Clinical discovery","Search the archive","Find visits by patient, RM, diagnosis, procedure, DPJP, operator, or assistant operator.")
    q=st.text_input("Search",placeholder="Try: odontektomi, an operator name, DPJP, diagnosis, or RM…",label_visibility="collapsed")
    if not q:
        st.markdown('<div class="cv-empty"><div class="icon">⌕</div><b>Search across structured Drive metadata</b><br>New CaseVault uploads are indexed by clinical and care-team fields.</div>',unsafe_allow_html=True);return
    if q and EMBEDDED_MODE:
        try:
            with st.spinner("Searching Drive metadata…"):drive=drive_service();patient_rows=drive_patients();rows=search_catalog(patient_rows,drive.list_visit_metadata(),q)
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
                    if url:b.link_button("OPEN  ↗",url,use_container_width=True)
            if not rows:st.info("No matching Drive metadata. Legacy folders without SOAP metadata can only be searched by patient folder name or RM.")
        except Exception as exc:st.error(f"Drive search failed: {exc}")
    elif q:
        for p in api("GET","/search",params={"q":q}) or []:st.markdown(f'<div class="cv-card"><b>{p["name"]}</b><br><span class="muted">RM {p["rm"]} · {p.get("hospital") or "—"}</span></div>',unsafe_allow_html=True)

def settings_page():
    page_header("Workspace","Settings & connection","Review the active Google account, Drive connection, and privacy boundary.")
    h=api("GET","/health") or {}
    c1,c2,c3=st.columns(3)
    c1.metric("CaseVault service","Online" if h.get("status")=="ok" else "Unavailable")
    c2.metric("Google OAuth","Connected" if st.session_state.get("google_user") else "Not connected")
    c3.metric("Drive root","Configured" if drive_configured() else "Missing")
    if EMBEDDED_MODE:
        if not oauth_configured():st.warning("Tambahkan GOOGLE_CLIENT_ID dan GOOGLE_CLIENT_SECRET ke Streamlit Secrets.")
        elif not st.session_state.get("google_user"):begin_google_login()
        else:
            with st.container(border=True):
                st.markdown(f"**Google account**  \n{st.session_state.google_user['email']}")
                st.caption("Patient folders and new visit files are read from and written to the configured private Drive root.")
            if st.button("SIGN OUT OF CASEVAULT"):
                session_id=st.query_params.get("cv_session")
                if session_id:
                    with oauth_session_db() as db:db.execute("DELETE FROM oauth_sessions WHERE id=?",(session_id,))
                st.session_state.pop("google_credentials",None);st.session_state.pop("google_user",None);st.query_params.clear();st.rerun()
        if not drive_configured():st.warning("GOOGLE_DRIVE_ROOT_FOLDER_ID belum diisi.")
    else:st.markdown(f"[Sign in with Google]({API}/auth/login)")
    st.info("CaseVault never creates public sharing links and sends no clinical data to AI services. Google Drive is the source of truth for the cloud deployment.")

page=sidebar()
if EMBEDDED_MODE and oauth_configured() and not st.session_state.get("google_user"):
    left,center,right=st.columns([1,1.25,1])
    with center:
        st.markdown('<div style="height:8vh"></div><div class="cv-brand" style="justify-content:center"><div class="cv-logo">CV</div><div><strong style="color:#12263a">OMFS CaseVault</strong><span style="color:#607785">Private clinical archive</span></div></div>',unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("## Welcome back")
            st.caption("Sign in with an authorized Google account to open the Drive-backed case archive.")
            begin_google_login()
            st.markdown('<div class="cv-meta" style="text-align:center;margin-top:.8rem">Private by default · No public links · No clinical AI processing</div>',unsafe_allow_html=True)
else:
    {"＋  New visit":quick_upload,"◫  Patient folders":patients,"⌕  Clinical search":search,"⚙  Settings":settings_page}[page]()
